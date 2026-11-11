"""Sorting, skipping, undo, and copy tracking mixin for Session."""

from __future__ import annotations

import os
import sys
import threading
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import library
from .copy_index import load_copied_index, save_copied_index
from .journal import Journal
from .models import SKIP_SLOT, Action, SessionError, Track
from . import worker
from .worker import Worker, _is_empty


def _get_move() -> Callable[[Path, Path], None]:
    mod = sys.modules.get("bakkis_sorter.session")
    return getattr(mod, "_move", worker._move)


def _get_copy() -> Callable[[Path, Path], None]:
    mod = sys.modules.get("bakkis_sorter.session")
    return getattr(mod, "_copy", worker._copy)


class SessionActionsMixin:
    """Sorting, skipping, undo execution, and copy-mode bookkeeping operations."""

    if TYPE_CHECKING:
        lock: threading.RLock
        music_root: Path
        queue: list[Track]
        total: int
        history: list[Action]
        counts: dict[str, int]
        count_info: dict[str, dict]
        session_id: str
        presets: Any
        _settings: Callable[[], dict]
        _today: Callable[[], date]
        _journal: Journal
        _worker: Worker
        _action_ids: Any
        _copied_index: Optional[dict]

        def _changed(self) -> None: ...
        def notify(self, level: str, message: str, **extra: object) -> None: ...

    def operation(self) -> str:
        """"move" or "copy" for the next sort; a folder inside a sorted tree is always moved."""
        op = "copy" if self._settings().get("file_operation") == "copy" else "move"
        return library.effective_operation(self.music_root, op)

    def active_dests(self) -> frozenset:
        """Destinations of sorts this session can still undo (history uses Undo for these)."""
        with self.lock:
            return frozenset(str(a.dest) for a in self.history if a.dest)

    def sort(self, track_id: str, mode: str, slot: int) -> Action:
        """Sort the current track to a decision slot of the active preset of ``mode``."""
        with self.lock:
            preset = self.presets.active(mode)
            n = len(preset["labels"])
            if not 0 <= int(slot) < n:
                raise SessionError(f"Slot {slot} out of range")
            return self._start_action(track_id, preset, int(slot))

    def skip(self, track_id: str, mode: str) -> Action:
        """Move the current track to ``…/Skipped/<relative subfolders>`` of the active preset."""
        with self.lock:
            return self._start_action(track_id, self.presets.active(mode), SKIP_SLOT)

    def _start_action(self, track_id: str, preset: dict, slot: int) -> Action:
        if not self.queue:
            raise SessionError("Queue is empty")
        head = self.queue[0]
        if head.id != str(track_id):
            raise SessionError("Track is no longer current")
        if not library.is_within(head.path, self.music_root):
            raise SessionError(f"{head.path.name} is not inside the loaded folder")
        s = self._settings()
        dated = bool(preset.get("dated"))
        op = self.operation()
        today = self._today()
        if slot == SKIP_SLOT:
            dest_dir = library.skipped_destination_dir(self.music_root, preset, head.path, dated=dated, today=today, op=op)
            label = "Skipped"
        else:
            dest_dir = library.slot_destination_dir(self.music_root, preset, slot, dated=dated, today=today, op=op)
            label = preset["labels"][slot]
        a = Action(
            id=next(self._action_ids),
            track=head,
            mode=preset["mode"],
            slot=slot,
            label=label,
            preset_id=preset["id"],
            preset_name=preset["name"],
            op=op,
            src=head.path,
            dest_dir=dest_dir,
            cleanup=op == "move" and bool(s.get("cleanup_empty_source_folders_after_sort")),
        )
        self.queue.pop(0)
        self.history.append(a)
        self.counts[a.count_key] = self.counts.get(a.count_key, 0) + 1
        self.count_info.setdefault(
            a.count_key,
            {
                "label": a.label,
                "preset": a.preset_name,
                "color": None if slot == SKIP_SLOT else preset["colors"][slot],
                "slot": slot,
                "mode": a.mode,
            },
        )
        self._worker.submit(lambda: self._run_action(a))
        return a

    def _run_action(self, a: Action) -> None:
        with self.lock:
            if a.undo_requested:
                a.status = "cancelled"  # undone before it ran; the file never moves
                return
        error: Optional[str] = None
        dest: Optional[Path] = None
        try:
            if not a.src.exists():
                raise FileNotFoundError(f"{a.src.name} is not there any more")
            a.dest_dir.mkdir(parents=True, exist_ok=True)
            dest = library.reserve_destination(a.dest_dir, a.src.name)
            if a.op == "copy":
                _get_copy()(a.src, dest)
            else:
                _get_move()(a.src, dest)
        except OSError as e:
            error = str(e)
            if dest is not None and dest.exists() and (a.src.exists() or _is_empty(dest)):
                try:
                    dest.unlink()
                except OSError:
                    pass

        if error is not None:
            library.prune_sorted_dirs(a.dest_dir, self.music_root)
            gone = not a.src.exists()
            with self.lock:
                a.status = "failed"
                if a in self.history:
                    self.history.remove(a)
                if not a.undo_requested:
                    self.counts[a.count_key] = max(0, self.counts.get(a.count_key, 0) - 1)
                    if gone:
                        self.total = max(0, self.total - 1)
                    else:
                        self.queue.insert(min(1, len(self.queue)), a.track)
            self._journal_safe({"event": "failed", **self._journal_fields(a), "error": error})
            if gone and not a.undo_requested:
                self.notify("warning", f"{a.src.name} is not there any more, so it was removed from the queue", action_id=a.id)
            else:
                self.notify("error", f"Could not {a.op} {a.src.name}: {error}", action_id=a.id)
            return

        deleted: list[Path] = []
        if a.cleanup:
            deleted, cleanup_err = library.cleanup_empty_parents(a.src, self.music_root)
            if cleanup_err:
                self.notify("warning", cleanup_err)
        if a.op == "copy":
            self._record_copy(a.src, dest)

        with self.lock:
            a.dest = dest
            a.deleted_dirs = deleted
            a.status = "done"
            if a.op == "move" and not a.undo_requested:
                a.track.path = dest
        self._journal_safe({"event": "sort", **self._journal_fields(a)})
        self._changed()

    def undo_last(self) -> Optional[Action]:
        """Undo the most recent action. The track becomes current again immediately."""
        with self.lock:
            if not self.history:
                return None
            a = self.history.pop()
            a.undo_requested = True
            a.track.path = a.src
            self.queue.insert(0, a.track)
            self.counts[a.count_key] = max(0, self.counts.get(a.count_key, 0) - 1)
            self._worker.submit(lambda: self._run_undo(a))
            return a

    def undo_all(self) -> int:
        """Undo every action of this session (newest first). The UI must confirm before calling."""
        n = 0
        while self.undo_last() is not None:
            n += 1
        return n

    def _run_undo(self, a: Action) -> None:
        if a.status != "done" or a.dest is None:
            return
        restored: Optional[Path] = None
        try:
            if a.op == "copy":
                if a.dest.exists():
                    if not a.src.exists():
                        raise OSError("the original is gone, so the copy is kept")
                    if a.dest.stat().st_size != a.src.stat().st_size:
                        raise OSError(f"{a.dest.name} was changed since it was copied; it is left in place")
                    a.dest.unlink()
                self._forget_copy(a.src)
                restored = a.src
            else:
                if not a.dest.exists():
                    raise FileNotFoundError(f"{a.dest.name} is no longer in {a.dest.parent.name}")
                a.src.parent.mkdir(parents=True, exist_ok=True)
                restored = library.reserve_destination(a.src.parent, a.src.name)
                _get_move()(a.dest, restored)
        except OSError as e:
            if a.op == "move" and restored is not None and restored.exists() and (a.dest.exists() or _is_empty(restored)):
                try:
                    restored.unlink()
                except OSError:
                    pass
            with self.lock:
                if a.track in self.queue:
                    self.queue.remove(a.track)
                a.track.path = a.dest if a.op == "move" else a.src
                a.undo_requested = False
                self.history.append(a)
                self.counts[a.count_key] = self.counts.get(a.count_key, 0) + 1
            self._journal_safe({"event": "undo_failed", **self._journal_fields(a), "error": str(e)})
            self.notify("error", f"Could not undo {a.src.name}: {e}", action_id=a.id)
            return

        library.prune_sorted_dirs(a.dest.parent, self.music_root)
        with self.lock:
            a.track.path = restored
        if restored != a.src:
            self.notify("warning", f"{a.src.name} already existed; restored as {restored.name}", action_id=a.id)
        self._journal_safe({"event": "undo", **self._journal_fields(a), "restored": str(restored)})
        self._changed()

    # ------------------------------------------------------------------ copy index

    def _record_copy(self, src: Path, dest: Path) -> None:
        if self._copied_index is None:
            self._copied_index = load_copied_index(self.music_root)
        try:
            size = os.path.getsize(dest)
        except OSError:
            size = None
        self._copied_index[library.relative_posix(src, self.music_root)] = {
            "dest": library.relative_posix(dest, self.music_root),
            "size": size,
        }
        self._save_copied_index()

    def _forget_copy(self, src: Path) -> None:
        if self._copied_index is None:
            self._copied_index = load_copied_index(self.music_root)
        if self._copied_index.pop(library.relative_posix(src, self.music_root), None) is not None:
            self._save_copied_index()

    def _save_copied_index(self) -> None:
        try:
            save_copied_index(self.music_root, self._copied_index or {})
        except OSError as e:
            self.notify("warning", f"Could not save copy index: {e}")

    # ------------------------------------------------------------------ journal

    def _journal_fields(self, a: Action) -> dict:
        return {
            "session": self.session_id,
            "root": str(self.music_root),
            "op": a.op,
            "mode": a.mode,
            "preset": a.preset_name,
            "label": a.label,
            "slot": a.slot,
            "src": str(a.src),
            "dest": str(a.dest) if a.dest else None,
            "deleted_dirs": [str(d) for d in a.deleted_dirs],
        }

    def _journal_safe(self, entry: dict) -> None:
        try:
            self._journal.append(entry)
        except OSError as e:
            self.notify("warning", f"Could not write journal: {e}")
