"""Embedded artwork extraction and image downsampling."""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    from mutagen.flac import Picture
except ImportError:  # pragma: no cover
    Picture = None

from .reader import _open

MAX_COVER_BYTES = 5 * 1024 * 1024
META_CACHE_SIZE = 2000
COVER_MAX_DIM = 320
COVER_THUMB_QUALITY = 82
COVER_CACHE_ITEMS = 128
COVER_CACHE_BYTES = 8 * 1024 * 1024
COVER_CACHE_SIZE = COVER_CACHE_ITEMS
PREFETCH_WORKERS = 3

def _size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


# --------------------------------------------------------------------------- cover art


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _pick(pics: list) -> Optional[bytes]:
    """Prefer the front cover (picture type 3)."""
    if not pics:
        return None
    chosen = next((p for p in pics if getattr(p, "type", None) == 3), pics[0])
    data = getattr(chosen, "data", chosen)
    if isinstance(data, (bytes, bytearray, memoryview)):
        b = bytes(data)
        return b if 0 < len(b) <= MAX_COVER_BYTES else None
    return None


def _cover_from_audio(audio: object) -> Optional[bytes]:
    try:
        # FLAC
        b = _pick(list(getattr(audio, "pictures", None) or []))
        if b:
            return b
        tags = getattr(audio, "tags", None)
        if not tags:
            return None
        # MP4 / M4A
        if hasattr(tags, "get") and "covr" in tags:
            b = _pick(list(tags.get("covr") or []))
            if b:
                return b
        # ID3 (mp3, aiff, wav)
        if hasattr(tags, "getall"):
            b = _pick(list(tags.getall("APIC")))
            if b:
                return b
        # Ogg Vorbis / Opus: base64 FLAC picture blocks
        if Picture is not None and hasattr(tags, "get"):
            blocks = tags.get("metadata_block_picture") or tags.get("METADATA_BLOCK_PICTURE") or []
            pics = []
            for raw in blocks:
                try:
                    pics.append(Picture(base64.b64decode(raw)))
                except Exception:  # noqa: BLE001
                    continue
            return _pick(pics)
    except Exception:  # noqa: BLE001
        return None
    return None


def read_cover(path: Path) -> Optional[bytes]:
    audio = _open(path)
    return _cover_from_audio(audio) if audio is not None else None


def downsample_cover(
    raw_bytes: Optional[bytes],
    max_dim: int = COVER_MAX_DIM,
    quality: int = COVER_THUMB_QUALITY,
) -> Optional[bytes]:
    """Downsample large embedded artwork to a crisp, lightweight thumbnail (max 320x320, ~15-25KB)."""
    if not raw_bytes or Image is None:
        return raw_bytes
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            w, h = im.size
            if w <= max_dim and h <= max_dim:
                return raw_bytes
            im = im.convert("RGB")
            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception:
        return raw_bytes


# --------------------------------------------------------------------------- cache


