"""Audio metadata via mutagen: duration, title/artist, BPM, key and embedded cover art.

Results are cached in memory keyed by (path, size, mtime) so a moved or replaced file is re-read.
``MetadataCache.prefetch`` reads upcoming tracks on a small thread pool.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote

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
    size: int = 0

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
    """``Artist - Title.ext`` → (title, artist), cleaned for display."""
    stem = clean_text(Path(path).stem) or ""
    return split_artist_title(stem)


# Download-site stamps that end up in tags and file names ("myfreemp3.vip", "www.electronicfresh.com").
_SITE_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|ru|vip|info|biz|me|cc|to|io|fm|pw|ws|club|top|site|xyz|eu|us|tv|su)\b(?:/\S*)?"
)
_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_TRACKNO_RE = re.compile(r"^\d{1,3}\s*-\s*(?=\D|\d{1,2}[AB]\b)")
_EMPTY_BRACKETS_RE = re.compile(r"[\(\[]\s*[\)\]]")
# Key / BPM stamps written into titles by DJ tools ("… 7A", "8A - 124") and rating stars.
_TRAILING_KEY_RE = re.compile(r"\s+(?:\d{1,2}[AB])(?:\s*-?\s*\d{2,3})?$")
_KEYISH_RE = re.compile(r"^\d{1,2}[AB](?:\s*-?\s*\d{2,3})?$")
_STARS_RE = re.compile(r"\*{3,}")
_LEADING_KEY_RE = re.compile(r"^(?:\d{1,2}[AB](?:\s+or\s+\d{1,2}[AB])?)(?:\s*-?\s*\d{2,3})?\s*-\s*")


def clean_text(value: Optional[str]) -> Optional[str]:
    """Display cleanup only (files and tags are never changed).

    Removes URL escapes, download-site stamps, "08-" track prefixes, underscores, rating stars and
    trailing Camelot key / BPM stamps.
    """
    if not value:
        return None
    s = unquote(value) if _ESCAPE_RE.search(value) else value
    s = s.replace("_", " ")
    s = _SITE_RE.sub(" ", s)
    s = _STARS_RE.sub(" ", s)
    s = _EMPTY_BRACKETS_RE.sub(" ", s)
    s = _TRACKNO_RE.sub("", s.strip())
    s = _LEADING_KEY_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" _-–—|.,")
    s = _TRAILING_KEY_RE.sub("", s)
    if _KEYISH_RE.match(s):
        return None  # the whole field is just a key/BPM stamp
    return s or None


_ARTIST_TITLE_SEPS = (" - ", " – ", " — ")


def split_artist_title(text: str) -> tuple[Optional[str], Optional[str]]:
    """``Artist - Title`` (also with – or —) → (title, artist)."""
    for sep in _ARTIST_TITLE_SEPS:
        left, found, right = text.partition(sep)
        if found and left.strip() and right.strip():
            return right.strip(), left.strip()
    return None, None


def display_line(path: Path, title: Optional[str], artist: Optional[str]) -> str:
    if title and artist:
        return f"{title} — {artist}"
    if title or artist:
        return title or artist  # type: ignore[return-value]
    ft, fa = parse_filename(path)
    if ft and fa:
        return f"{ft} — {fa}"
    return clean_text(Path(path).stem) or Path(path).stem


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
        title = clean_text(_tag(audio, TITLE_KEYS))
        artist = clean_text(_tag(audio, ARTIST_KEYS))
        if title and not artist:
            # Title tags like "Artist - Title" (often when the artist tag was a site stamp).
            t, a = split_artist_title(title)
            if t:
                title, artist = t, a
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
        size=_size(path),
    )


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


# --------------------------------------------------------------------------- waveform peaks (computed by the UI)

MAX_PEAKS_LEN = 8192


class PeakStore:
    """Disk cache of waveform peaks (base64 of 0–255 bytes) keyed by path + size + mtime."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _file(self, path: Path) -> Optional[Path]:
        key = _cache_key(path)
        if key is None:
            return None
        digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"

    def get(self, path: Path) -> Optional[str]:
        f = self._file(path)
        if f is None:
            return None
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            peaks = data.get("peaks") if isinstance(data, dict) else None
            return peaks if isinstance(peaks, str) else None
        except (OSError, ValueError):
            return None

    def put(self, path: Path, peaks: str) -> bool:
        if not isinstance(peaks, str) or len(peaks) > MAX_PEAKS_LEN:
            return False
        try:
            base64.b64decode(peaks, validate=True)
        except ValueError:
            return False
        f = self._file(path)
        if f is None:
            return False
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"file": str(path), "peaks": peaks}), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            return False
        return True
