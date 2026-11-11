"""Mutagen audio reader and metadata extraction."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - mutagen is a hard dependency in requirements
    MutagenFile = None

from .tags import (
    ARTIST_KEYS,
    BPM_KEYS,
    KEY_KEYS,
    TITLE_KEYS,
    TrackMeta,
    _tag,
    clean_text,
    display_line,
    format_bpm,
    parse_filename,
    split_artist_title,
)

def _open(path: Path):
    if MutagenFile is None:
        return None
    try:
        return MutagenFile(str(path))
    except Exception:  # noqa: BLE001 - corrupt files must never break the app
        return None


def _cache_key(path: Path) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_size, st.st_mtime_ns)


def file_digest(path: Path) -> Optional[str]:
    """Generate SHA-1 cache digest based on path, size, and mtime."""
    key = _cache_key(path)
    if key is None:
        return None
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()


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

    from ..analyzer import analyze_audio_file, to_camelot
    from .cover import _cover_from_audio, _size

    camelot = to_camelot(key)
    drop_seconds: Optional[float] = None
    jump_delta = 15.24
    detected_bpm: Optional[float] = None

    digest = file_digest(path)
    st = None
    try:
        from ..storage import get_storage

        st = get_storage()
    except Exception:
        pass

    analysis = None
    if st is not None and digest:
        try:
            analysis = st.get_analysis(digest)
        except Exception:
            pass

    if analysis:
        detected_bpm = float(analysis["bpm"])
        if not camelot and analysis.get("camelot_key"):
            camelot = str(analysis["camelot_key"])
        drop_seconds = analysis.get("drop_seconds")
        jump_delta = float(analysis.get("jump_delta") or 15.24)
    elif path.is_file():
        try:
            res = analyze_audio_file(path)
            detected_bpm = res.bpm
            if not camelot:
                camelot = res.camelot_key
            drop_seconds = res.drop_seconds
            jump_delta = res.jump_delta
            if st is not None and digest:
                try:
                    st.put_analysis(
                        file_hash=digest,
                        bpm=res.bpm,
                        musical_key=res.musical_key,
                        camelot_key=res.camelot_key,
                        drop_seconds=res.drop_seconds,
                        jump_delta=res.jump_delta,
                        confidence=res.confidence,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    if bpm:
        try:
            val = float(bpm)
            if val > 40:
                jump_delta = round(32.0 * (60.0 / val), 2)
        except (ValueError, TypeError):
            pass
    elif detected_bpm and jump_delta == 15.24:
        jump_delta = round(32.0 * (60.0 / detected_bpm), 2)

    return TrackMeta(
        duration=duration,
        title=title,
        artist=artist,
        display=display_line(path, title, artist),
        bpm=bpm,
        key=key,
        has_cover=_cover_from_audio(audio) is not None if audio is not None else False,
        size=_size(path),
        camelot=camelot,
        drop_seconds=drop_seconds,
        jump_delta=jump_delta,
        detected_bpm=detected_bpm,
    )


