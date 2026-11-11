"""High-performance audio analysis engine backed by libsonare (C++17 DSP).

Computes accurate BPM (with multi-layer onset tracking for acapellas and dubs),
musical key (with Camelot wheel mapping), song structure, and first drop positions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from .logger import get_logger

logger = get_logger("analyzer")

try:
    import libsonare
except ImportError:
    libsonare = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- Camelot Wheel Mapping
# Maps standard musical key descriptions to Camelot codes (e.g., "A minor" -> "8A", "C major" -> "8B")

CAMELOT_MAP: dict[str, str] = {
    # Major keys (B)
    "c major": "8B", "c": "8B",
    "g major": "9B", "g": "9B",
    "d major": "10B", "d": "10B",
    "a major": "11B", "a": "11B",
    "e major": "12B", "e": "12B",
    "b major": "1B", "b": "1B",
    "f# major": "2B", "f#": "2B", "gb major": "2B", "gb": "2B",
    "c# major": "3B", "c#": "3B", "db major": "3B", "db": "3B",
    "g# major": "4B", "g#": "4B", "ab major": "4B", "ab": "4B",
    "d# major": "5B", "d#": "5B", "eb major": "5B", "eb": "5B",
    "a# major": "6B", "a#": "6B", "bb major": "6B", "bb": "6B",
    "f major": "7B", "f": "7B",

    # Minor keys (A)
    "a minor": "8A", "am": "8A",
    "e minor": "9A", "em": "9A",
    "b minor": "10A", "bm": "10A",
    "f# minor": "11A", "f#m": "11A", "gbm": "11A",
    "c# minor": "12A", "c#m": "12A", "dbm": "12A",
    "g# minor": "1A", "g#m": "1A", "abm": "1A",
    "d# minor": "2A", "d#m": "2A", "ebm": "2A",
    "a# minor": "3A", "a#m": "3A", "bbm": "3A",
    "f minor": "4A", "fm": "4A",
    "c minor": "5A", "cm": "5A",
    "g minor": "6A", "gm": "6A",
    "d minor": "7A", "dm": "7A",
}


def to_camelot(key_str: Optional[str]) -> Optional[str]:
    """Convert musical key description to Camelot wheel notation (e.g., 'A minor' -> '8A')."""
    if not key_str:
        return None
    cleaned = key_str.strip().lower()
    if cleaned in CAMELOT_MAP:
        return CAMELOT_MAP[cleaned]
    # Check if already in Camelot format (e.g. "8A", "11B")
    import re
    if re.fullmatch(r"\d{1,2}[AB]", key_str.strip().upper()):
        return key_str.strip().upper()
    return None


@dataclass(frozen=True)
class TrackAnalysis:
    bpm: float
    musical_key: Optional[str] = None
    camelot_key: Optional[str] = None
    drop_seconds: Optional[float] = None
    confidence: float = 0.0
    jump_delta: float = 15.24  # 32 beats @ 126 BPM default

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_dj_bpm(bpm: float) -> float:
    """Fold BPM into standard electronic/club tempo range (85 - 175 BPM)."""
    if bpm <= 0:
        return 126.0
    val = float(bpm)
    while val < 85.0:
        val *= 2.0
    while val > 175.0:
        val /= 2.0
    return round(val, 1)


def analyze_audio_file(path: Path | str) -> TrackAnalysis:
    """Analyze audio file using libsonare C++ engine, returning BPM, Key, Drop 1, and 32-bar jump delta."""
    p = Path(path)
    if not p.is_file():
        return TrackAnalysis(bpm=126.0, jump_delta=15.24)

    if libsonare is None:
        logger.debug("libsonare not available; using default tempo")
        return TrackAnalysis(bpm=126.0, jump_delta=15.24)

    ext = p.suffix.lower()
    temp_wav: Optional[Path] = None

    try:
        # libsonare natively reads WAV and MP3.
        # AIFF, AAC, and other formats are converted via macOS /usr/bin/afconvert.
        if ext in (".mp3", ".wav"):
            audio_source_path = str(p)
        else:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                temp_wav = Path(tf.name)
            try:
                subprocess.run(
                    ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@44100", str(p), str(temp_wav)],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                audio_source_path = str(temp_wav)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("afconvert failed for %s: %s; falling back to direct load", p, exc)
                audio_source_path = str(p)

        audio = libsonare.Audio.from_file(audio_source_path)
        res = audio.analyze()

        raw_bpm = float(getattr(res, "bpm", 0.0) or 0.0)
        if raw_bpm <= 0.0:
            raw_bpm = float(audio.detect_bpm() or 126.0)
        bpm = normalize_dj_bpm(raw_bpm)

        raw_key = str(getattr(res, "key", "") or "").strip()
        if not raw_key:
            raw_key = str(audio.detect_key() or "").strip()
        musical_key = raw_key or None
        camelot_key = to_camelot(musical_key)

        confidence = float(getattr(res, "bpm_confidence", 0.0) or 0.0)

        # Detect Drop 1 from section boundaries
        drop_sec: Optional[float] = None
        sections = getattr(res, "sections", None) or []
        for s in sections:
            stype = getattr(s, "type", None)
            s_name = str(getattr(s, "canonical_name", "") or "").lower()
            start = float(getattr(s, "start", 0.0) or 0.0)
            # Find the first high-energy chorus / instrumental drop after the intro
            if start >= 20.0 and (
                "chorus" in s_name
                or "drop" in s_name
                or "instrumental" in s_name
                or (hasattr(libsonare, "SectionType") and stype in (libsonare.SectionType.CHORUS, libsonare.SectionType.INSTRUMENTAL))
            ):
                drop_sec = round(start, 2)
                break

        # Calculate exact 32-beat phrase jump duration
        jump_delta = round(32.0 * (60.0 / bpm), 2)

        return TrackAnalysis(
            bpm=bpm,
            musical_key=musical_key,
            camelot_key=camelot_key,
            drop_seconds=drop_sec,
            confidence=confidence,
            jump_delta=jump_delta,
        )

    except Exception as exc:  # noqa: BLE001 - non-fatal, fallback to standard tempo
        logger.debug("libsonare analysis failed for %s: %s", p, exc)
        return TrackAnalysis(bpm=126.0, jump_delta=15.24)
    finally:
        if temp_wav and temp_wav.exists():
            try:
                temp_wav.unlink()
            except OSError:
                pass
