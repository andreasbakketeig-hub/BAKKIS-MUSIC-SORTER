"""'Copy feedback': a plain-text summary of a session's decisions for promo reports and demo replies.

    Promos – 23 Oct 2026

    Feeling it (3)
    • Jimpster – Sleeper
    ...

Skips are left out (not a verdict). Swipe groups are ordered best first: ↑ (Favorite slot), → (Keep slot),
← (Archive slot); grid groups follow the tiles. Track names come from the tags (cleaned) or the file name.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .metadata import TrackMeta

SWIPE_RANK = {2: 0, 0: 1, 1: 2}  # ↑ first, then →, then ←


def _track_line(item: dict, get_meta: Callable[[Path], TrackMeta]) -> str:
    for p in (item.get("dest"), item.get("src")):
        if p and Path(p).exists():
            m = get_meta(Path(p))
            if m.artist and m.title:
                return f"{m.artist} – {m.title}"
            return m.display
    return Path(item.get("src") or item.get("dest") or "?").stem


def _rank(item: dict, presets: list) -> tuple:
    slot = item.get("slot")
    if slot is None:  # journal entries from before slots were logged: find the label in the preset
        p = next((x for x in presets if x.get("name") == item.get("preset")), None)
        if p and item.get("label") in p.get("labels", []):
            slot = p["labels"].index(item["label"])
    if slot is None:
        return (99, str(item.get("label")))
    if item.get("mode") == "grid":
        return (int(slot), "")
    return (SWIPE_RANK.get(int(slot), 9), "")


def _date(ts: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(str(ts)).strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return ""


def build(items: list[dict], get_meta: Callable[[Path], TrackMeta], presets: list) -> dict:
    """{"text", "count"} for the given history items (one session)."""
    verdicts = [i for i in items if i.get("slot") != -1 and i.get("label") != "Skipped"]
    blocks: dict[str, dict[str, list]] = {}
    ranks: dict[tuple, tuple] = {}
    first_ts: dict[str, str] = {}
    for it in verdicts:
        preset = it.get("preset") or "Sorted"
        label = it.get("label") or "?"
        blocks.setdefault(preset, {}).setdefault(label, []).append(_track_line(it, get_meta))
        ranks.setdefault((preset, label), _rank(it, presets))
        first_ts.setdefault(preset, it.get("ts"))
    parts = []
    for preset, groups in blocks.items():
        head = f"{preset} – {_date(first_ts[preset])}".rstrip(" –")
        lines = [head]
        for label in sorted(groups, key=lambda lb: ranks[(preset, lb)]):
            names = groups[label]
            lines += ["", f"{label} ({len(names)})"] + [f"• {n}" for n in names]
        parts.append("\n".join(lines))
    return {"text": "\n\n\n".join(parts), "count": len(verdicts)}
