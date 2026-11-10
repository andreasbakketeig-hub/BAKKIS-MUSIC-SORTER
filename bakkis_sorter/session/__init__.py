"""Sorting session: the queue, optimistic sort/skip/undo and a single background worker for file ops."""

from __future__ import annotations

import itertools
import os
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from .. import library
from .actions import SessionActionsMixin
from .copy_index import (
    COPIED_INDEX_NAME,
    copied_index_path,
    filter_already_copied,
    load_copied_index,
    save_copied_index,
)
from .journal import Journal
from .models import SKIP_SLOT, Action, SessionError, Track, new_track
from .queue import SessionQueueMixin
from .worker import Worker, _copy, _is_empty, _move

CLOSE_TIMEOUT = 30.0  # seconds to wait for pending file ops when closing
HISTORY_PREVIEW = 5
UPCOMING_PREVIEW = 8
MAX_NOTICES = 200


class Session(SessionQueueMixin, SessionActionsMixin):
    """Composed sorting session state machine."""

    def __init__(
        self,
        music_root: Path,
        paths: list[Path],
        *,
        presets,  # config.PresetStore; read under self.lock
        settings: Callable[[], dict],
        journal: Optional[Journal] = None,
        on_change: Optional[Callable[[], None]] = None,
        today: Callable[[], date] = date.today,
        lock: Optional[threading.RLock] = None,
    ) -> None:
        self.music_root = Path(music_root).resolve()
        self.presets = presets
        self._settings = settings
        self._journal = journal or Journal()
        self._on_change = on_change
        self._today = today
        self.lock = lock or threading.RLock()
        self._action_ids = itertools.count(1)
        self.tracks: dict[str, Track] = {}
        self.queue: list[Track] = [self._new_track(p) for p in paths]
        self.total = len(self.queue)
        self.held: list[Track] = []
        self.filter: Optional[dict] = None
        self.history: list[Action] = []
        self.counts: dict[str, int] = {}
        self.count_info: dict[str, dict] = {}
        self.notices: deque = deque(maxlen=MAX_NOTICES)
        self._notice_seq = itertools.count(1)
        self.started_at = time.time()
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._copied_index: Optional[dict] = None
        self._worker = Worker(on_error=lambda e: self.notify("error", f"File operation failed: {e}"))

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001 - never let UI callbacks kill the worker
                pass

    def notify(self, level: str, message: str, **extra: object) -> None:
        with self.lock:
            self.notices.append({"seq": next(self._notice_seq), "level": level, "message": message, **extra})
        self._changed()

    def notices_since(self, seq: int) -> list[dict]:
        with self.lock:
            return [n for n in self.notices if n["seq"] > seq]

    def folder_count(self, preset: dict, slot: int) -> int:
        """Audio files currently in a slot's destination folder (today's folder when dated)."""
        d = library.slot_destination_dir(
            self.music_root, preset, slot, dated=bool(preset.get("dated")), today=self._today(), op=self.operation()
        )
        try:
            return sum(1 for e in os.scandir(d) if e.is_file() and library.is_audio_path(Path(e.name)))
        except OSError:
            return 0

    def snapshot(self, upcoming: int = UPCOMING_PREVIEW) -> dict:
        with self.lock:
            done = self.total - len(self.queue) - len(self.held)
            return {
                "root": str(self.music_root),
                "total": self.total,
                "position": min(done + 1, self.total),
                "remaining": len(self.queue) + len(self.held),
                "held": len(self.held),
                "filter": self.filter,
                "current": self._track_public(self.queue[0]) if self.queue else None,
                "upcoming": [self._track_public(t) for t in self.queue[1 : 1 + upcoming]],
                "history": [a.public() for a in self.history[-HISTORY_PREVIEW:]][::-1],
                "counts": dict(self.counts),
                "count_info": dict(self.count_info),
                "sorted": sum(self.counts.values()),
                "pending": sum(1 for a in self.history if a.status == "pending"),
                "elapsed": time.time() - self.started_at,
                "last_notice": self.notices[-1]["seq"] if self.notices else 0,
                "operation": self.operation(),
                "in_place": library.sorts_in_place(self.music_root),
            }

    def flush(self) -> None:
        self._worker.flush()

    def close(self, timeout: Optional[float] = CLOSE_TIMEOUT) -> bool:
        """Finish pending file ops (up to ``timeout`` s, so a dead network disk can't block quitting)."""
        done = self._worker.flush(timeout)
        self._worker.stop()
        return done


__all__ = [
    "CLOSE_TIMEOUT",
    "COPIED_INDEX_NAME",
    "HISTORY_PREVIEW",
    "MAX_NOTICES",
    "SKIP_SLOT",
    "UPCOMING_PREVIEW",
    "Action",
    "Journal",
    "Session",
    "SessionActionsMixin",
    "SessionError",
    "SessionQueueMixin",
    "Track",
    "Worker",
    "_copy",
    "_is_empty",
    "_move",
    "copied_index_path",
    "filter_already_copied",
    "load_copied_index",
    "new_track",
    "save_copied_index",
]
