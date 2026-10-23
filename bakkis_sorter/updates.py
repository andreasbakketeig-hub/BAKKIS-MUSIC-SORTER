"""Check GitHub Releases for a newer version. Read-only, one request, never downloads anything."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional

from . import __version__

RELEASES_URL = "https://api.github.com/repos/andreasbakketeig-hub/BAKKIS-MUSIC-SORTER/releases/latest"
TIMEOUT = 4.0


def parse_version(text: str) -> tuple:
    """'v2.1.0' -> (2, 1, 0, 1); '2.0.0.dev0' -> (2, 0, 0, 0). Pre-releases sort before releases."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", str(text).strip())
    if not m:
        return (0, 0, 0, 0)
    major, minor, patch, rest = m.groups()
    final = 0 if re.search(r"(dev|a|b|rc|alpha|beta)", rest or "", re.IGNORECASE) else 1
    return (int(major), int(minor or 0), int(patch or 0), final)


def check(current: str = __version__, url: str = RELEASES_URL, opener=urllib.request.urlopen) -> dict:
    """{"current", "latest", "newer", "url"} or {"current", "error"}."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "BAKKIS-MUSIC-SORTER"})
    try:
        with opener(req, timeout=TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - offline, private repo, rate limit: all mean "no info"
        return {"current": current, "error": str(e)}
    tag: Optional[str] = data.get("tag_name") if isinstance(data, dict) else None
    if not tag or data.get("draft") or data.get("prerelease"):
        return {"current": current, "error": "no release"}
    return {
        "current": current,
        "latest": tag.lstrip("v"),
        "newer": parse_version(tag) > parse_version(current),
        "url": data.get("html_url") or "",
    }
