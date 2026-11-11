"""Data models for sorting sessions: Track, Action, and SessionError."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SKIP_SLOT = -1
_TRACK_IDS = itertools.count(1)


class SessionError(Exception):
    """Invalid request from the UI (stale track id, empty queue, bad slot)."""


@dataclass
class Track:
    id: str
    path: Path
    rel_posix_lower: Optional[str] = None


def new_track(path: Path) -> Track:
    """A track with a process-unique id (used by sorting sessions and listen mode)."""
    p = Path(path)
    return Track(id=str(next(_TRACK_IDS)), path=p, rel_posix_lower=None)


@dataclass
class Action:
    id: int
    track: Track
    mode: str  # "swipe" | "grid"
    slot: int  # SKIP_SLOT for skip
    label: str
    preset_id: str
    preset_name: str
    op: str  # "move" | "copy"
    src: Path
    dest_dir: Path
    cleanup: bool
    dest: Optional[Path] = None
    deleted_dirs: list = field(default_factory=list)
    status: str = "pending"  # pending | done | failed | cancelled
    undo_requested: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def count_key(self) -> str:
        return "skip" if self.slot == SKIP_SLOT else f"{self.preset_id}:{self.slot}"

    def public(self) -> dict:
        return {
            "id": self.id,
            "track_id": self.track.id,
            "name": self.src.name,
            "label": self.label,
            "mode": self.mode,
            "slot": self.slot,
            "op": self.op,
            "status": self.status,
            "dest": str(self.dest) if self.dest else None,
        }
