"""Audio metadata via mutagen: duration, title/artist, BPM, key and embedded cover art.

Results are cached in memory keyed by (path, size, mtime) so a moved or replaced file is re-read.
``MetadataCache.prefetch`` reads upcoming tracks on a small thread pool.
"""

from __future__ import annotations

import base64
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

try:
    from mutagen import File as MutagenFile
    from mutagen.flac import Picture
except ImportError:  # pragma: no cover - mutagen is a hard dependency in requirements
    MutagenFile = None
    Picture = None

TITLE_KEYS = ("title", "TITLE", "TIT2", "\xa9nam", "Title")
ARTIST_KEYS = ("artist", "ARTIST", "TPE1", "\xa9ART", "Artist")
BPM_KEYS = ("TBPM", "bpm", "BPM", "tmpo")
KEY_KEYS = ("TKEY", "key", "initialkey", "KEY", "INITIALKEY", "----:com.apple.iTunes:initialkey")

MAX_COVER_BYTES = 5 * 1024 * 1024
META_CACHE_SIZE = 2000
COVER_CACHE_SIZE = 24
PREFETCH_WORKERS = 3


@dataclass(frozen=True)
class TrackMeta:
    duration: float
    title: Optional[str]
    artist: Optional[str]
    display: str  # "Title — Artist", parsed filename, or file name
    bpm: Optional[str]
    key: Optional[str]
    has_cover: bool

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- tag helpers


def _scalar(val: object) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        if not val:
            return None
        val = val[0]
    if hasattr(val, "text"):
        raw = getattr(val, "text")
        if isinstance(raw, list):
            parts = [str(x).strip() for x in raw if str(x).strip()]
            return " / ".join(parts) if parts else None
        val = raw
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    s = str(val).strip()
    return s or None


def _tag(audio: object, keys: tuple[str, ...]) -> Optional[str]:
    tags = getattr(audio, "tags", None)
    for k in keys:
        for src in (audio, tags):
            if src is None or not hasattr(src, "get"):
                continue
            try:
                out = _scalar(src.get(k))
            except Exception:  # noqa: BLE001 - mutagen tag types raise various errors on odd keys
                out = None
            if out:
                return out
    return None


def parse_filename(path: Path) -> tuple[Optional[str], Optional[str]]:
    """``Artist - Title.ext`` → (title, artist)."""
    left, sep, right = Path(path).stem.strip().partition(" - ")
    if not sep or not left.strip() or not right.strip():
        return None, None
    return right.strip(), left.strip()


def display_line(path: Path, title: Optional[str], artist: Optional[str]) -> str:
    if title and artist:
        return f"{title} — {artist}"
    if title or artist:
        return title or artist  # type: ignore[return-value]
    ft, fa = parse_filename(path)
    if ft and fa:
        return f"{ft} — {fa}"
    return Path(path).name


def format_bpm(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        f = float(str(raw).replace(",", ".").strip())
    except ValueError:
        return str(raw).strip() or None
    if f <= 0:
        return None
    if abs(f - round(f)) < 0.02:
        return str(int(round(f)))
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _open(path: Path):
    if MutagenFile is None:
        return None
    try:
        return MutagenFile(str(path))
    except Exception:  # noqa: BLE001 - corrupt files must never break the app
        return None


def read_meta(path: Path) -> TrackMeta:
    audio = _open(path)
    duration = 0.0
    title = artist = bpm = key = None
    if audio is not None:
        try:
            duration = float(getattr(audio.info, "length", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            duration = 0.0
        title = _tag(audio, TITLE_KEYS)
        artist = _tag(audio, ARTIST_KEYS)
        bpm = format_bpm(_tag(audio, BPM_KEYS))
        key = _tag(audio, KEY_KEYS)
    return TrackMeta(
        duration=duration,
        title=title,
        artist=artist,
        display=display_line(path, title, artist),
        bpm=bpm,
        key=key,
        has_cover=_cover_from_audio(audio) is not None if audio is not None else False,
    )


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


# --------------------------------------------------------------------------- cache


def _cache_key(path: Path) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_size, st.st_mtime_ns)


class _LRU:
    def __init__(self, size: int) -> None:
        self.size = size
        self._d: "OrderedDict[tuple, object]" = OrderedDict()

    def get(self, key: tuple):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def __contains__(self, key: tuple) -> bool:
        return key in self._d

    def put(self, key: tuple, value: object) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)


_MISSING = object()


class MetadataCache:
    def __init__(self, workers: int = PREFETCH_WORKERS) -> None:
        self._lock = threading.Lock()
        self._meta = _LRU(META_CACHE_SIZE)
        self._covers = _LRU(COVER_CACHE_SIZE)
        self._inflight: set = set()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bakkis-meta")

    def get(self, path: Path) -> TrackMeta:
        key = _cache_key(path)
        if key is None:
            return TrackMeta(0.0, None, None, Path(path).name, None, None, False)
        with self._lock:
            hit = self._meta.get(key)
        if hit is not None:
            return hit  # type: ignore[return-value]
        meta = read_meta(path)
        with self._lock:
            self._meta.put(key, meta)
        return meta

    def cover(self, path: Path) -> Optional[bytes]:
        key = _cache_key(path)
        if key is None:
            return None
        with self._lock:
            hit = self._covers.get(key)
        if hit is not None:
            return None if hit is _MISSING else hit  # type: ignore[return-value]
        data = read_cover(path)
        with self._lock:
            self._covers.put(key, data if data is not None else _MISSING)
        return data

    def prefetch(self, paths: Iterable[Path], covers: int = 2) -> None:
        """Read metadata for ``paths`` in the background; also covers for the first ``covers`` paths."""
        for i, p in enumerate(paths):
            key = _cache_key(p)
            if key is None:
                continue
            with self._lock:
                if key in self._meta or key in self._inflight:
                    continue
                self._inflight.add(key)
            self._pool.submit(self._prefetch_one, Path(p), key, i < covers)

    def _prefetch_one(self, path: Path, key: tuple, with_cover: bool) -> None:
        try:
            self.get(path)
            if with_cover:
                self.cover(path)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
