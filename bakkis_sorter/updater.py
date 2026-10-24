"""One-click updates for the packaged app: download, verify, unpack, swap, relaunch.

Flow: ``Installer.start(release)`` downloads the release zip on a background thread, checks its SHA-256
against the digest GitHub publishes for the asset, unpacks it next to the installed app (same volume, so
the swap is a rename) and checks the bundle id and version. ``Installer.restart()`` then starts a small
helper script that waits for this process to exit, swaps the bundles (keeping the old one until the new one
is in place) and opens the new app. Settings, presets and history live outside the app and are untouched.

A download made by the app itself carries no quarantine flag, so the new version opens without the
"Open Anyway" step a browser download needs. It only ever runs when the user asks for it.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

BUNDLE_ID = "com.bakkis.music-sorter"
ASSET_RE = re.compile(r"^BAKKIS-MUSIC-SORTER-[\w.]+\.zip$")
CHUNK = 256 * 1024
TIMEOUT = 30.0
STAGING_PREFIX = ".bakkis-update-"


class UpdateError(Exception):
    pass


def app_bundle() -> Optional[Path]:
    """The running .app (``…/X.app/Contents/MacOS/exe``), or None when running from source."""
    override = os.environ.get("BAKKIS_UPDATE_BUNDLE")  # tests
    if override:
        return Path(override)
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve()
    bundle = p.parents[2] if len(p.parents) > 2 else None
    return bundle if bundle is not None and bundle.suffix == ".app" else None


MOVE_HINT = "Drag BAKKIS MUSIC SORTER to your Applications folder to update with one click."


def runs_from_outside_applications(bundle: Optional[Path]) -> bool:
    """Opened straight from the download (macOS runs a hidden read-only copy) or from the mounted DMG."""
    if bundle is None:
        return False
    s = str(bundle)
    return "/AppTranslocation/" in s or s.startswith("/Volumes/")


def in_applications(bundle: Optional[Path]) -> bool:
    return bundle is not None and bundle.parent in (Path("/Applications"), Path.home() / "Applications")


def install_problem(bundle: Optional[Path]) -> Optional[str]:
    """Why an in-place update can't happen (None if it can)."""
    if bundle is None:
        return "Updates install from the app itself; when running from source, use git pull."
    if runs_from_outside_applications(bundle):
        return MOVE_HINT
    if not bundle.is_dir() or not os.access(bundle.parent, os.W_OK):
        return f"No permission to replace the app in {bundle.parent}. {MOVE_HINT}"
    return None


def pick_asset(release: dict) -> Optional[dict]:
    """The zip asset with a published SHA-256: {"name", "url", "sha256", "size"}."""
    for a in release.get("assets") or []:
        name, digest = a.get("name") or "", a.get("digest") or ""
        if ASSET_RE.match(name) and digest.startswith("sha256:") and a.get("browser_download_url"):
            return {"name": name, "url": a["browser_download_url"], "sha256": digest.split(":", 1)[1].lower(), "size": a.get("size") or 0}
    return None


def bundle_version(app: Path) -> tuple[str, str]:
    """(CFBundleIdentifier, CFBundleShortVersionString) of an .app."""
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    return str(info.get("CFBundleIdentifier", "")), str(info.get("CFBundleShortVersionString", ""))


def helper_script(old: Path, new: Path, backup: Path, staging: Path, pid: int, open_cmd: str = "open") -> str:
    """Bash that waits for ``pid`` to exit, swaps ``new`` into ``old`` (rolling back on failure) and opens it.
    If the app is still running after two minutes it changes nothing (the leftovers are cleaned up later)."""
    q = shlex.quote
    return f"""#!/bin/bash
for i in $(seq 1 1200); do kill -0 {pid} 2>/dev/null || break; sleep 0.1; done
kill -0 {pid} 2>/dev/null && exit 0
[ -e {q(str(backup))} ] && exit 0
if mv {q(str(old))} {q(str(backup))}; then
  if mv {q(str(new))} {q(str(old))}; then
    rm -rf {q(str(backup))}
  else
    mv {q(str(backup))} {q(str(old))}
  fi
fi
rm -rf {q(str(staging))}
{open_cmd} {q(str(old))}
"""


def _begin_activity(reason: str):
    """Keep macOS from throttling the download (App Nap) if the window goes to the background."""
    try:
        from Foundation import NSProcessInfo

        return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(0x00FFFFFF, reason)  # user initiated
    except Exception:  # noqa: BLE001 - only a speed-up
        return None


def _end_activity(token) -> None:
    if token is not None:
        try:
            from Foundation import NSProcessInfo

            NSProcessInfo.processInfo().endActivity_(token)
        except Exception:  # noqa: BLE001
            pass


class Installer:
    """Background download + verify; one at a time. ``state`` is what the UI polls."""

    def __init__(self, bundle: Optional[Path], opener: Callable = urllib.request.urlopen,
                 unzip: Optional[Callable[[Path, Path], None]] = None) -> None:
        self.bundle = bundle
        self._opener = opener
        self._unzip = unzip or (lambda z, d: subprocess.run(["ditto", "-x", "-k", str(z), str(d)], check=True, capture_output=True))
        self._lock = threading.Lock()
        self.state: dict = {"status": "idle", "progress": 0.0, "error": None, "version": None}
        self._staging: Optional[Path] = None
        self._new_app: Optional[Path] = None
        self.cleanup_leftovers()

    def cleanup_leftovers(self, older_than: float = 600.0) -> None:
        """Remove staging folders and old bundles an earlier update left next to the app (quit mid-download,
        never restarted, a failed swap). Only our own ``.bakkis-update-*`` names, and only stale ones."""
        if self.bundle is None or install_problem(self.bundle) is not None:
            return
        now = time.time()
        try:
            for p in self.bundle.parent.iterdir():
                if p.name.startswith(STAGING_PREFIX) and p != self._staging and now - p.stat().st_mtime > older_than:
                    shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def _set(self, **kw) -> None:
        with self._lock:
            self.state.update(kw)

    def start(self, release: dict, version: str) -> dict:
        problem = install_problem(self.bundle)
        if problem:
            raise UpdateError(problem)
        asset = pick_asset(release)
        if asset is None:
            raise UpdateError("This release has no verified download.")
        with self._lock:
            if self.state["status"] in ("downloading", "unpacking", "ready", "restarting"):
                return dict(self.state)
            self.state = {"status": "downloading", "progress": 0.0, "error": None, "version": version}
        self.cleanup_leftovers()
        threading.Thread(target=self._run, args=(asset, version), name="bakkis-update", daemon=True).start()
        return self.snapshot()

    def _run(self, asset: dict, version: str) -> None:
        assert self.bundle is not None
        staging = self.bundle.parent / f"{STAGING_PREFIX}{os.getpid()}-{time.time_ns()}"
        activity = _begin_activity("Downloading an update")
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            self._staging = staging
            zpath = staging / asset["name"]
            digest = hashlib.sha256()
            req = urllib.request.Request(asset["url"], headers={"User-Agent": "BAKKIS-MUSIC-SORTER"})
            with self._opener(req, timeout=TIMEOUT) as res, open(zpath, "wb") as out:
                total = int(res.headers.get("Content-Length") or asset["size"] or 0)
                done = 0
                while True:
                    chunk = res.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if total:
                        self._set(progress=min(0.99, done / total))
            if digest.hexdigest() != asset["sha256"]:
                raise UpdateError("The download did not match its checksum; nothing was changed.")
            self._set(status="unpacking")
            unpacked = staging / "unpacked"
            unpacked.mkdir()
            self._unzip(zpath, unpacked)
            apps = [p for p in unpacked.iterdir() if p.suffix == ".app"]
            if len(apps) != 1:
                raise UpdateError("The download did not contain the app.")
            bid, ver = bundle_version(apps[0])
            if bid != BUNDLE_ID or ver != version:
                raise UpdateError(f"Unexpected app in the download ({bid} {ver}).")
            zpath.unlink()
            self._new_app = apps[0]
            self._set(status="ready", progress=1.0)
        except Exception as e:  # noqa: BLE001 - reported to the UI; the installed app is untouched
            shutil.rmtree(staging, ignore_errors=True)
            self._staging = None
            self._set(status="error", error=str(e) if isinstance(e, UpdateError) else f"Update failed: {e}")
        finally:
            _end_activity(activity)

    def restart(self, pid: Optional[int] = None, open_cmd: str = "open") -> Path:
        """Start the swap helper (it waits for this process to exit). Returns the script path."""
        with self._lock:  # exactly one helper, even if Update is confirmed twice at once
            if self.state["status"] != "ready" or self._new_app is None or self._staging is None or self.bundle is None:
                raise UpdateError("The update is not ready yet.")
            self.state["status"] = "restarting"
        try:
            backup = self.bundle.parent / f"{STAGING_PREFIX}old-{os.getpid()}-{time.time_ns()}.app"
            script = self._staging / "swap.sh"
            script.write_text(helper_script(self.bundle, self._new_app, backup, self._staging, pid or os.getpid(), open_cmd))
            script.chmod(0o755)
            subprocess.Popen(["/bin/bash", str(script)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self._set(status="ready")
            raise UpdateError(f"Could not start the update: {e}") from e
        return script
