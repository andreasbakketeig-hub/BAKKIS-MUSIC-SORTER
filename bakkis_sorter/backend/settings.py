"""Settings, presets, updater, and Listen/Discover mode API mixin for AppBackend."""

from __future__ import annotations

import base64
import platform
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional
from urllib.parse import urlsplit

from .. import __version__, keys, listen, playlists, updater, updates, waveform
from ..config import MODES, norm_mode, reset_settings, save_settings
from ..metadata import MetadataCache, PeakStore
from ..server import ApiError
from ..session import Journal, Session

if TYPE_CHECKING:
    from ..config import PresetStore
    from ..duplicates import DuplicateIndex

OPEN_URL_HOSTS = frozenset({
    "bakkis.com",
    "www.bakkis.com",
    "ko-fi.com",
    "github.com",
    "open.spotify.com",
    "soundcloud.com",
    "www.instagram.com",
    "instagram.com",
    "beatport.com",
    "www.beatport.com",
    "www.traxsource.com",
    "traxsource.com",
    "songshare.com",
    "inflyteapp.com",
    "www.inflyteapp.com",
    "my.trackstack.app",
    "trackstack.app",
})
OPEN_URL_MAIL = "bakkis@bakkis.com"


class SettingsApiMixin:
    """API methods for presets, persistent settings, software updates, and Listen mode."""

    if TYPE_CHECKING:
        lock: threading.RLock
        settings: dict
        presets: PresetStore
        meta: MetadataCache
        peaks: PeakStore
        journal: Journal
        session: Optional[Session]
        dupes: Optional[DuplicateIndex]
        listen: Optional[listen.ListenSession]
        updater: updater.Installer
        scan: dict
        _native: dict[str, Callable[..., object]]
        _update_result: Optional[dict]
        _hearts_store: Optional[listen.Hearts]

        def _changed(self) -> None: ...
        def _open_in_finder(self, path: Path) -> None: ...
        def audio_path(self, track_id: str) -> Optional[Path]: ...
        def _session(self) -> Session: ...

    # ------------------------------------------------------------------ state

    def api_state(self) -> dict:
        with self.lock:
            snap = self.session.snapshot() if self.session else None
            return {
                "version": __version__,
                "settings": dict(self.settings),
                "presets": self.presets.data,
                "scan": dict(self.scan),
                "session": snap,
                "default_folder_exists": self._default_folder_exists(),
                # Packaged app opened from the download or the DMG: the UI suggests moving it (once).
                "move_hint": updater.MOVE_HINT if updater.runs_from_outside_applications(self.updater.bundle) else None,
                "home": str(Path.home()),
            }

    def _default_folder_exists(self) -> bool:
        p = self.settings.get("default_folder_path")
        return bool(p) and Path(p).expanduser().is_dir()

    def api_notices(self, since: int = 0) -> list:
        s = self.session
        return s.notices_since(int(since)) if s else []

    def api_track_meta(self, track_id: str) -> dict:
        if getattr(self.session, "is_inflyte", False):
            return self.session.track_meta(track_id)  # type: ignore[attr-defined]
        p = self.audio_path(track_id)
        if p is None:
            raise ApiError("Unknown track", 404)
        meta = self.meta.get(p)
        dupes = self.dupes
        dup = dupes.find(p, meta) if dupes is not None else None
        return {"track_id": track_id, **meta.to_dict(), "duplicate": dup, "pcm": waveform.is_pcm_path(p)}

    def api_peaks_get(self, track_id: str) -> Optional[str]:
        """Cached peaks; uncompressed WAV/AIFF are computed via byte-stride slicing."""
        p = self.audio_path(track_id)
        if not p:
            return None
        cached = self.peaks.get_bytes(p)
        if cached is None:
            raw = waveform.compute(p)
            if raw is not None:
                self.peaks.put_bytes(p, raw)
                cached = raw
        return base64.b64encode(cached).decode("ascii") if cached is not None else None

    def api_peaks_put(self, track_id: str, peaks: str) -> bool:
        p = self.audio_path(track_id)
        return self.peaks.put(p, peaks) if p else False

    def api_open_url(self, url: str) -> bool:
        """Open a link in the user's browser / mail app. Only https: and mailto: links are allowed."""
        parts = urlsplit(str(url))
        ok = (parts.scheme == "https" and (parts.hostname or "") in OPEN_URL_HOSTS) or (
            parts.scheme == "mailto" and parts.path.lower() == OPEN_URL_MAIL
        )
        if not ok:
            raise ApiError("This link can't be opened from the app")
        self._open_external(str(url))
        return True

    def _open_external(self, url: str) -> None:
        subprocess.Popen(["open", url])

    # ------------------------------------------------------------------ listen mode (Discover; never moves files)

    def api_listen_sources(self) -> list:
        s = self._session()
        with s.lock:
            unsorted = len(s.queue)
        name = s.music_root.name
        out = [{"key": "queue", "label": f"Unsorted tracks in {name}", "count": unsorted}] if unsorted else []
        return out + listen.sorted_sources(s.music_root)

    def api_listen_start(self, sources: list, shuffle: bool = True) -> dict:
        s = self._session()
        labels = {x["key"]: x["label"] for x in self.api_listen_sources()}
        items: list = []
        for key in [str(k) for k in sources]:
            if key == "queue":
                with s.lock:
                    items += [(t.path, labels.get("queue", "Unsorted")) for t in s.queue]
            else:
                folder = listen.resolve_source(s.music_root, key)
                if folder is None:
                    raise ApiError(f"Unknown source: {key}")
                items += [(p, labels.get(key, folder.name)) for p in playlists._audio_files(folder)]  # noqa: SLF001
        if not items:
            raise ApiError("Nothing to listen to in the chosen folders", 409)
        self.listen = listen.ListenSession(items, shuffle=bool(shuffle))
        self.meta.prefetch([t.path for t in self.listen.tracks[:8]])
        hearted = {i["path"] for i in self._hearts().items}
        return {"total": len(self.listen.tracks), "tracks": self.listen.public(s.music_root, hearted)}

    def _hearts(self) -> listen.Hearts:
        if getattr(self, "_hearts_store", None) is None:
            self._hearts_store = listen.Hearts()
        return self._hearts_store

    def api_listen_heart(self, track_id: str, on: bool = True) -> dict:
        """Heart / un-heart the playing Discover track."""
        p = self.listen.track_path(track_id) if self.listen else None
        if p is None:
            raise ApiError("Not a Discover track", 404)
        source = self.listen.source_of.get(str(track_id), "") if self.listen else ""
        state = self._hearts().set(p, bool(on), source)
        return {"hearted": state, "count": len(self._hearts().items)}

    def api_hearts(self) -> list:
        return self._hearts().public(lambda p: listen.current_location(p, self.journal))

    def api_hearts_remove(self, path: str) -> int:
        self._hearts().set(Path(path), False)
        return len(self._hearts().items)

    def api_hearts_clear(self) -> bool:
        self._hearts().clear()
        return True

    def api_hearts_create_folder(self, name: Optional[str] = None, clear: bool = True) -> dict:
        s = self._session()
        res = listen.create_folder(s.music_root, self._hearts(), self.journal, name)
        if clear and res["copied"]:
            self._hearts().clear()
        return res

    def api_hearts_export_playlist(self) -> dict:
        s = self._session()
        items = [Path(i["current"]) for i in self.api_hearts() if i["current"]]
        return playlists.write_playlist(s.music_root, "Discover - Hearted", items, self.meta.get)

    def api_listen_prefetch(self, track_ids: list) -> bool:
        if self.listen:
            self.meta.prefetch([p for p in (self.listen.track_path(t) for t in track_ids) if p])
        return True

    def api_listen_stop(self) -> bool:
        self.listen = None
        return True

    def api_export_playlists(self) -> dict:
        """Advanced: M3U8 per destination folder into BAKKIS_SORTED/_Playlists."""
        s = self._session()
        s.flush()  # include sorts that are still being written
        return playlists.export(s.music_root, self.meta.get)

    def api_check_update(self) -> dict:
        """Ask GitHub Releases once per launch whether a newer version exists (only if enabled)."""
        if not self.settings.get("check_for_updates", True):
            return {"current": __version__, "error": "disabled"}
        if getattr(self, "_update_result", None) is None:
            self._update_result = updates.check()
        r = {k: v for k, v in self._update_result.items() if k != "release"}
        if r.get("newer"):
            problem = updater.install_problem(self.updater.bundle)
            r.update(can_install=problem is None, install_problem=problem)
        return r

    def api_update_install(self) -> dict:
        """Download and verify the newer version in the background (the UI polls update_status)."""
        r = getattr(self, "_update_result", None) or {}
        if not r.get("newer"):
            raise ApiError("No newer version to install")
        try:
            return self.updater.start(r.get("release") or {}, r["latest"])
        except updater.UpdateError as e:
            raise ApiError(str(e), 409) from e

    def api_show_applications(self) -> str:
        """Open the Applications folder in Finder (for the 'drag the app there' hint)."""
        self._open_in_finder(Path("/Applications"))
        return "/Applications"

    def api_update_status(self) -> dict:
        return self.updater.snapshot()

    def api_update_restart(self) -> dict:
        """Swap in the downloaded version and quit; the helper reopens the app once this process has exited."""
        try:
            self.updater.restart()
        except updater.UpdateError as e:
            raise ApiError(str(e), 409) from e
        quit_app = self._native.get("quit_app")
        if quit_app is not None:
            threading.Timer(0.3, quit_app).start()  # let this response reach the UI first
        return self.updater.snapshot()

    def api_about(self) -> dict:
        return {
            "version": __version__,
            "macos": platform.mac_ver()[0] or platform.platform(),
            "python": platform.python_version(),
        }

    # ------------------------------------------------------------------ settings

    def api_reset_settings(self) -> dict:
        """Settings back to defaults; presets, history and the default folder stay."""
        with self.lock:
            self.settings = save_settings(reset_settings(self.settings))
            out = dict(self.settings)
        self._changed()
        return out

    def api_set_settings(self, **changes: object) -> dict:
        with self.lock:
            merged = dict(self.settings)
            merged.update(changes)
            self.settings = save_settings(merged)
            out = dict(self.settings)
        self._changed()
        return out

    # ------------------------------------------------------------------ presets

    def api_preset_select(self, preset_id: str) -> dict:
        with self.lock:
            p = self.presets.set_active(preset_id)
        self._changed()
        return p

    def api_preset_create(
        self,
        mode: str,
        name: str,
        labels: list,
        colors: list,
        folders: Optional[list] = None,
        dated: Optional[bool] = None,
    ) -> dict:
        if norm_mode(mode) not in MODES:
            raise ApiError("Bad mode")
        with self.lock:
            if dated is None:
                dated = self.settings["create_dated_sorting_sessions"]
            p = self.presets.create(mode, name, labels, colors, folders, dated=bool(dated))
        self._changed()
        return p

    def api_preset_update(self, preset_id: str, **fields: object) -> dict:
        with self.lock:
            p = self.presets.update(preset_id, **fields)
        self._changed()
        return p

    def api_preset_delete(self, preset_id: str) -> dict:
        with self.lock:
            self.presets.delete(preset_id)
            data = self.presets.data
        self._changed()
        return data

    def api_preset_reset_grid(self, preset_id: str) -> dict:
        with self.lock:
            p = self.presets.reset_grid_to_default_genres(preset_id)
        self._changed()
        return p
