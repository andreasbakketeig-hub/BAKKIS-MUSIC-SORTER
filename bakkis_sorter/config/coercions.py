"""Primitive data sanitizers, palette helpers, and mode constants."""
from __future__ import annotations

import re

MODES = ("swipe", "grid")
SWIPE_SLOTS = 3
GRID_SLOTS = 9

# Auto-play start choices. Tracks shorter than start + 30 s start about a third of the way in (see app.js).
PLAYBACK_START_OPTIONS_SEC: tuple[float, ...] = (0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 240.0, 300.0, 360.0)
DEFAULT_PLAYBACK_START_SEC = 120.0

DEFAULT_SWIPE_LABELS = ["Keep", "Archive", "Favorite"]
DEFAULT_SWIPE_COLORS = ["#00D6B4", "#FF2D55", "#FFF200"]
DEFAULT_SWIPE_FOLDERS = ["KEEP", "ARCHIVE", "FAVORITES"]

DEFAULT_GENRE_LABELS = [
    "House",
    "Tech House",
    "Deep House",
    "Minimal",
    "Afro House",
    "Melodic",
    "Techno",
    "Garage",
    "Other",
]

# Fixed palette for grid tiles (name, hex).
GENRE_PALETTE: tuple[tuple[str, str], ...] = (
    ("Teal", "#00D6B4"),
    ("Blue", "#18A0FB"),
    ("Purple", "#7C5CFF"),
    ("Gray", "#8A8F98"),
    ("Orange", "#FF7A1A"),
    ("Pink", "#D65CFF"),
    ("Red", "#FF2D55"),
    ("Yellow", "#FFF200"),
    ("Dark", "#2A2F36"),
)
GENRE_PALETTE_HEX_SET = frozenset(hx.upper() for _, hx in GENRE_PALETTE)
DEFAULT_GENRE_COLORS = [hx for _, hx in GENRE_PALETTE]


# --------------------------------------------------------------------------- paths



def coerce_bool(val: object) -> bool:
    """JSON / hand-edited booleans (``bool("false")`` would be wrong)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def normalize_hex(h: object, fallback: str = DEFAULT_GENRE_COLORS[0]) -> str:
    s = str(h or "").strip()
    if not s:
        return fallback.upper()
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    try:
        int(s[1:], 16)
    except ValueError:
        return fallback.upper()
    return s.upper() if len(s) == 7 else fallback.upper()


def coerce_palette_hex(h: object, fallback: str) -> str:
    n = normalize_hex(h, fallback)
    if n in GENRE_PALETTE_HEX_SET:
        return n
    fb = normalize_hex(fallback)
    return fb if fb in GENRE_PALETTE_HEX_SET else DEFAULT_GENRE_COLORS[0]


def safe_segment(name: object, fallback: str = "Preset") -> str:
    """Sanitize one path segment (preset, bucket, genre folder names)."""
    s = str(name or "").strip().replace("/", "-").replace("\\", "-").replace(":", "-").replace("\x00", "")
    s = s.strip(". ") or fallback
    return s[:120]


def norm_mode(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in ("genre", "grid"):
        return "grid"
    return "swipe"


# --------------------------------------------------------------------------- settings


