"""File and download helpers for Trackstack promos."""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from ...config import config_dir
from ...logger import get_logger

logger = get_logger("trackstack")

DEFAULT_PROMO_DOWNLOAD_DIR = "~/Music/Inflyte Music"


def trackstack_cache_dir() -> Path:
    d = config_dir() / "trackstack_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def promo_download_dir(custom_dir: Optional[str] = None) -> Path:
    raw = (custom_dir or os.environ.get("BAKKIS_PROMO_DOWNLOAD_DIR") or DEFAULT_PROMO_DOWNLOAD_DIR).strip()
    d = Path(raw).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def stable_int_id(uuid_str: str) -> int:
    """Deterministic positive integer ID from a Trackstack UUID string."""
    digest = hashlib.sha256(str(uuid_str).encode("utf-8")).digest()
    return (int.from_bytes(digest[:6], "big") % 800_000_000) + 100_000_000


def split_display_title(display_title: str, fallback_sender: str = "") -> tuple[str, str]:
    """Split Trackstack ``displayTitle`` ( typically ``Track Title - Artist Name`` ) into ``(title, artist)``."""
    raw = str(display_title or "").strip()
    if not raw:
        return ("Untitled Promo", fallback_sender)
    if " - " in raw:
        parts = [p.strip() for p in raw.rsplit(" - ", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    if " – " in raw:
        parts = [p.strip() for p in raw.rsplit(" – ", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return (parts[0], parts[1])
    return (raw, fallback_sender)


def unique_download_dest(folder: Path, filename: str) -> Path:
    """Return a non-colliding path in ``folder`` so existing files in ``~/Music/Inflyte Music`` are never overwritten."""
    folder.mkdir(parents=True, exist_ok=True)
    clean_name = os.path.basename(str(filename or "").strip()).replace("/", "-").replace("\\", "-").replace("\x00", "")
    if not clean_name or clean_name.startswith("."):
        clean_name = f"trackstack_{uuid.uuid4().hex[:8]}.wav"
    candidate = folder / clean_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    idx = 2
    while True:
        alt = folder / f"{stem} ({idx}){suffix}"
        if not alt.exists():
            return alt
        idx += 1


def download_presigned_file(url: str, dest: Path) -> Optional[Path]:
    """Atomically stream a full-quality Trackstack file to ``dest`` without overwriting existing files."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out, length=256 * 1024)
        if tmp.stat().st_size > 0:
            os.replace(tmp, dest)
            return dest
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.error("Download failed for Trackstack URL %s -> %s: %s", url, dest, exc, exc_info=True)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    return None
