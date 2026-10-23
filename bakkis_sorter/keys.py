"""Musical keys as Camelot codes (1A–12B) for filtering and harmonic ordering."""

from __future__ import annotations

import re
from typing import Optional

# Camelot number for each minor (A) and major (B) key, by pitch class name.
_MINOR = {"G#": 1, "AB": 1, "D#": 2, "EB": 2, "A#": 3, "BB": 3, "F": 4, "C": 5, "G": 6, "D": 7, "A": 8, "E": 9, "B": 10, "F#": 11, "GB": 11, "C#": 12, "DB": 12}
_MAJOR = {"B": 1, "CB": 1, "F#": 2, "GB": 2, "C#": 3, "DB": 3, "G#": 4, "AB": 4, "D#": 5, "EB": 5, "A#": 6, "BB": 6, "F": 7, "C": 8, "G": 9, "D": 10, "A": 11, "E": 12}
NAMES = {
    **{f"{n}A": k for k, n in [("Abm", 1), ("Ebm", 2), ("Bbm", 3), ("Fm", 4), ("Cm", 5), ("Gm", 6), ("Dm", 7), ("Am", 8), ("Em", 9), ("Bm", 10), ("F#m", 11), ("C#m", 12)]},
    **{f"{n}B": k for k, n in [("B", 1), ("F#", 2), ("Db", 3), ("Ab", 4), ("Eb", 5), ("Bb", 6), ("F", 7), ("C", 8), ("G", 9), ("D", 10), ("A", 11), ("E", 12)]},
}

_CAMELOT_RE = re.compile(r"^(1[0-2]|0?[1-9])\s*([AB])$", re.IGNORECASE)
_OPENKEY_RE = re.compile(r"^(1[0-2]|0?[1-9])\s*([DM])$", re.IGNORECASE)
_MUSICAL_RE = re.compile(r"^([A-G])\s*([#♯b♭]?)\s*(m|min|minor|maj|major|moll|dur)?$", re.IGNORECASE)


def to_camelot(raw: Optional[str]) -> Optional[str]:
    """'8A', '08a', 'Am', 'A minor', 'F#m', 'Bb', '1m' (Open Key) -> Camelot code; None if unknown.

    Tags like '2A or 3A' or '8A - 124' use the first key.
    """
    if not raw:
        return None
    s = str(raw).strip().split(" or ")[0].split("/")[0].split(" - ")[0].strip()
    m = _CAMELOT_RE.match(s)
    if m:
        return f"{int(m.group(1))}{m.group(2).upper()}"
    m = _OPENKEY_RE.match(s)
    if m:  # Open Key: 1m = Am = 8A, 1d = C = 8B
        n = (int(m.group(1)) + 6) % 12 + 1
        return f"{n}{'A' if m.group(2).lower() == 'm' else 'B'}"
    m = _MUSICAL_RE.match(s)
    if m:
        note = m.group(1).upper() + {"♯": "#", "♭": "B", "b": "B", "#": "#", "": ""}[m.group(2)]
        q = (m.group(3) or "").lower()
        table = _MINOR if q in ("m", "min", "minor", "moll") else _MAJOR
        n = table.get(note)
        return f"{n}{'A' if table is _MINOR else 'B'}" if n else None
    return None


def compatible(code: str) -> set[str]:
    """Harmonic neighbours on the Camelot wheel: same key, ±1, and the relative major/minor."""
    m = _CAMELOT_RE.match(code)
    if not m:
        return set()
    n, letter = int(m.group(1)), m.group(2).upper()
    other = "B" if letter == "A" else "A"
    up, down = n % 12 + 1, (n - 2) % 12 + 1
    return {f"{n}{letter}", f"{up}{letter}", f"{down}{letter}", f"{n}{other}"}


def sort_key(code: Optional[str]) -> tuple:
    """Order around the wheel: 1A, 1B, 2A, …; unknown keys last."""
    m = _CAMELOT_RE.match(code or "")
    return (int(m.group(1)), m.group(2).upper()) if m else (99, "Z")
