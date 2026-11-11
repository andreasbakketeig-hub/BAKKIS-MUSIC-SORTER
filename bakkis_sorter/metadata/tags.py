"""Metadata dataclass, tag parsing, and text cleaning utilities."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

TITLE_KEYS = ("title", "TITLE", "TIT2", "\xa9nam", "Title")
ARTIST_KEYS = ("artist", "ARTIST", "TPE1", "\xa9ART", "Artist")
BPM_KEYS = ("TBPM", "bpm", "BPM", "tmpo")
KEY_KEYS = ("TKEY", "key", "initialkey", "KEY", "INITIALKEY", "----:com.apple.iTunes:initialkey")

MAX_COVER_BYTES = 5 * 1024 * 1024
META_CACHE_SIZE = 2000
COVER_MAX_DIM = 320
COVER_THUMB_QUALITY = 82
COVER_CACHE_ITEMS = 128
COVER_CACHE_BYTES = 8 * 1024 * 1024
COVER_CACHE_SIZE = COVER_CACHE_ITEMS
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
    camelot: Optional[str] = None
    drop_seconds: Optional[float] = None
    jump_delta: float = 15.24
    detected_bpm: Optional[float] = None

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

