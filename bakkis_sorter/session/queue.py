"""Queue management, fast searching, pagination, and filtering mixin for Session."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import library
from .models import SessionError, Track, new_track


class SessionQueueMixin:
    """Queue data structures, fast cached search, and filtering operations."""

    if TYPE_CHECKING:
        lock: threading.RLock
        music_root: Path
        tracks: dict[str, Track]
        queue: list[Track]
        total: int
        held: list[Track]
        filter: Optional[dict]
        _on_change: Optional[Callable[[], None]]

        def _changed(self) -> None: ...

    def _new_track(self, path: Path) -> Track:
        t = new_track(path)
        self.tracks[t.id] = t
        return t

    def _track_rel_posix(self, t: Track) -> str:
        """Fast cached lower-cased relative posix path for sub-millisecond search."""
        if t.rel_posix_lower is None:
            t.rel_posix_lower = library.relative_posix(t.path, self.music_root).lower()
        return t.rel_posix_lower

    def _track_public(self, t: Track) -> dict:
        return {
            "id": t.id,
            "name": t.path.name,
            "folder": library.source_folder_display(t.path, self.music_root),
            "rel": library.relative_posix(t.path, self.music_root),
        }

    def track_path(self, track_id: str) -> Optional[Path]:
        with self.lock:
            t = self.tracks.get(str(track_id))
            return t.path if t else None

    @property
    def current(self) -> Optional[Track]:
        with self.lock:
            return self.queue[0] if self.queue else None

    def set_order(self, order: str) -> None:
        with self.lock:
            self.queue = library.reorder_keep_current(self.queue, order, key=lambda t: str(t.path).lower())

    def play_next(self, track_id: str) -> None:
        """Move an upcoming track to position 2 (right after the current one)."""
        with self.lock:
            for i, t in enumerate(self.queue):
                if t.id == str(track_id):
                    if i > 1:
                        self.queue.insert(1, self.queue.pop(i))
                    return
            raise SessionError("Track not in queue")

    def add_next(self, path: Path) -> Track:
        """Queue a file as the next track (e.g. one put back from the history)."""
        with self.lock:
            t = self._new_track(path)
            self.queue.insert(min(1, len(self.queue)), t)
            self.total += 1
            return t

    def append_tracks(self, paths: list[Path]) -> list[Track]:
        """Append newly discovered tracks to the end of the queue as they stream in."""
        with self.lock:
            existing_paths = {t.path for t in self.tracks.values()}
            added = []
            for p in paths:
                p_resolved = Path(p).resolve()
                if p in existing_paths or p_resolved in existing_paths:
                    continue
                t = self._new_track(p)
                self.queue.append(t)
                existing_paths.add(p)
                existing_paths.add(p_resolved)
                self.total += 1
                added.append(t)
            if added:
                self._changed()
            return added

    def filter_pool(self) -> list[Track]:
        """Upcoming + set-aside tracks: what a new filter is applied to."""
        with self.lock:
            return self.queue[1:] + self.held

    def apply_filter(self, keep: set, order: Optional[list] = None, info: Optional[dict] = None) -> None:
        """Keep track ids in ``keep`` upcoming (in ``order`` if given, else queue order); set the rest aside."""
        with self.lock:
            pool = self.queue[1:] + sorted(self.held, key=lambda t: int(t.id))
            matched = [t for t in pool if t.id in keep]
            if order:
                rank = {tid: i for i, tid in enumerate(order)}
                matched.sort(key=lambda t: rank.get(t.id, len(rank)))
            self.held = [t for t in pool if t.id not in keep]
            self.queue = self.queue[:1] + matched
            self.filter = info or {}

    def clear_filter(self) -> None:
        """Bring set-aside tracks back; upcoming tracks return to the order the folder was loaded in."""
        with self.lock:
            upcoming = sorted(self.queue[1:] + self.held, key=lambda t: int(t.id))
            self.queue = self.queue[:1] + upcoming
            self.held = []
            self.filter = None

    def queue_slice(self, offset: int = 0, limit: int = 100, q: str = "") -> dict:
        """A page of upcoming tracks (the queue without the current track).

        ``q`` filters by file name and folder (all words must match, case-insensitive); ``index`` is
        always the track's position in the full upcoming list.
        """
        offset, limit = max(0, int(offset)), max(0, min(int(limit), 500))
        words = [w for w in str(q or "").lower().split() if w]
        with self.lock:
            indexed = list(enumerate(self.queue[1:]))
            if words:

                def hit(t: Track) -> bool:
                    hay = self._track_rel_posix(t)
                    return all(w in hay for w in words)

                indexed = [(i, t) for i, t in indexed if hit(t)]
            page = indexed[offset : offset + limit]
            items = [dict(self._track_public(t), index=i) for i, t in page]
            return {"total": len(indexed), "offset": offset, "items": items, "query": " ".join(words)}
