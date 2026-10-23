"""Sorting session: the queue, optimistic sort/skip/undo and a single background worker for file ops.

Threading model
- Public methods are called from HTTP request threads and return immediately. They update the queue and
  history under ``self.lock`` (optimistic UI) and enqueue a job for the worker.
- One worker thread runs file operations strictly in order, so undo always runs after the sort it undoes.
- When a sort fails the track is put back as *next* in the queue and a notice is emitted. When an undo
  fails the action goes back on the history and the track leaves the queue again.

Every completed operation is appended to a JSONL journal in the config dir.
"""

from __future__ import annotations

import itertools
import json
import uuid
import os
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from . import library
from .config import config_dir, write_json_atomic

COPIED_INDEX_NAME = ".bakkis_copied.json"
HISTORY_PREVIEW = 5
UPCOMING_PREVIEW = 8
MAX_NOTICES = 200

SKIP_SLOT = -1

# Track ids are unique per process so a stale audio URL from a previous folder never hits a new track.
_TRACK_IDS = itertools.count(1)


class SessionError(Exception):
    """Invalid request from the UI (stale track id, empty queue, bad slot)."""


@dataclass
class Track:
    id: str
    path: Path


def new_track(path: Path) -> Track:
    """A track with a process-unique id (used by sorting sessions and listen mode)."""
    return Track(id=str(next(_TRACK_IDS)), path=Path(path))


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


# --------------------------------------------------------------------------- file op primitives (patchable in tests)


def _move(src: Path, dest: Path) -> None:
    shutil.move(str(src), str(dest))


def _copy(src: Path, dest: Path) -> None:
    shutil.copy2(str(src), str(dest))


# --------------------------------------------------------------------------- copy-mode index


def copied_index_path(music_root: Path) -> Path:
    return library.sorted_root(music_root) / COPIED_INDEX_NAME


def load_copied_index(music_root: Path) -> dict:
    """``{relative source path: {"dest": str, "size": int}}`` of sources already copied by us."""
    try:
        with open(copied_index_path(music_root), encoding="utf-8") as f:
            data = json.load(f)
        src = data.get("sources") if isinstance(data, dict) else None
        return dict(src) if isinstance(src, dict) else {}
    except (OSError, ValueError):
        return {}


def save_copied_index(music_root: Path, index: dict) -> None:
    path = copied_index_path(music_root)
    if not index:
        # Nothing copied any more: no file, so an undone copy session leaves BAKKIS_SORTED clean.
        if path.exists():
            path.unlink()
        return
    write_json_atomic(path, {"version": 1, "sources": index})


def filter_already_copied(paths: list[Path], music_root: Path, index: Optional[dict] = None) -> list[Path]:
    """Drop sources recorded in the copy index whose size is unchanged (the file was not replaced)."""
    idx = load_copied_index(music_root) if index is None else index
    if not idx:
        return list(paths)
    out = []
    for p in paths:
        rec = idx.get(library.relative_posix(p, Path(music_root).resolve()))
        if rec is not None:
            try:
                if os.path.getsize(p) == rec.get("size"):
                    continue
            except OSError:
                pass
        out.append(p)
    return out


# --------------------------------------------------------------------------- journal


class Journal:
    """Append-only JSONL log per day: ``<config>/journal/YYYY-MM-DD.jsonl``."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory is not None else config_dir() / "journal"

    def path_for(self, day: date) -> Path:
        return self.directory / f"{day.isoformat()}.jsonl"

    def append(self, entry: dict) -> None:
        now = datetime.now()
        rec = {"ts": now.isoformat(timespec="seconds"), **entry}
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.path_for(now.date()), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- worker


class Worker:
    """Single daemon thread executing jobs in FIFO order."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="bakkis-fileops", daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], None]) -> None:
        self._q.put(job)

    def flush(self) -> None:
        """Block until every submitted job has finished."""
        self._q.join()

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                job()
            finally:
                self._q.task_done()


# --------------------------------------------------------------------------- session


class Session:
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
        # Share the owner's lock when given, so preset edits and sorts never interleave.
        self.lock = lock or threading.RLock()
        self._action_ids = itertools.count(1)
        self.tracks: dict[str, Track] = {}
        self.queue: list[Track] = [self._new_track(p) for p in paths]
        self.total = len(self.queue)
        # Advanced filter: tracks that don't match wait here (in queue order) until the filter is cleared.
        self.held: list[Track] = []
        self.filter: Optional[dict] = None
        self.history: list[Action] = []
        self.counts: dict[str, int] = {}
        # Label/preset/color per count key, so the summary survives a preset being edited or deleted.
        self.count_info: dict[str, dict] = {}
        self.notices: deque = deque(maxlen=MAX_NOTICES)
        self._notice_seq = itertools.count(1)
        self.started_at = time.time()
        self.session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._copied_index: Optional[dict] = None
        self._worker = Worker()

    # ------------------------------------------------------------------ helpers

    def _new_track(self, path: Path) -> Track:
        t = new_track(path)
        self.tracks[t.id] = t
        return t

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

    def track_path(self, track_id: str) -> Optional[Path]:
        with self.lock:
            t = self.tracks.get(str(track_id))
            return t.path if t else None

    @property
    def current(self) -> Optional[Track]:
        with self.lock:
            return self.queue[0] if self.queue else None

    def flush(self) -> None:
        self._worker.flush()

    def close(self) -> None:
        self._worker.flush()
        self._worker.stop()

    # ------------------------------------------------------------------ sort / skip

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
        s = self._settings()
        dated = bool(s.get("create_dated_sorting_sessions"))
        op = "copy" if s.get("file_operation") == "copy" else "move"
        today = self._today()
        if slot == SKIP_SLOT:
            dest_dir = library.skipped_destination_dir(self.music_root, preset, head.path, dated=dated, today=today)
            label = "Skipped"
        else:
            dest_dir = library.slot_destination_dir(self.music_root, preset, slot, dated=dated, today=today)
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
            a.dest_dir.mkdir(parents=True, exist_ok=True)
            dest = library.unique_destination(a.dest_dir, a.src.name)
            if a.op == "copy":
                _copy(a.src, dest)
            else:
                _move(a.src, dest)
        except OSError as e:
            error = str(e)
            # A failed cross-device move/copy can leave a partial file behind; remove it only if the source survived.
            if dest is not None and dest.exists() and a.src.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass

        if error is not None:
            library.prune_sorted_dirs(a.dest_dir, self.music_root)  # the folder we just made, if empty
            with self.lock:
                a.status = "failed"
                if a in self.history:
                    self.history.remove(a)
                if not a.undo_requested:
                    self.counts[a.count_key] = max(0, self.counts.get(a.count_key, 0) - 1)
                    # Put it back as *next*, so the track that is playing now is not interrupted.
                    self.queue.insert(min(1, len(self.queue)), a.track)
            self._journal_safe({"event": "failed", **self._journal_fields(a), "error": error})
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

    # ------------------------------------------------------------------ undo

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
            return  # the sort failed, so the file never left
        restored: Optional[Path] = None
        try:
            if a.op == "copy":
                if a.dest.exists():
                    a.dest.unlink()
                self._forget_copy(a.src)
                restored = a.src
            else:
                # Recreates any source folders the cleanup removed (they are all ancestors of src).
                a.src.parent.mkdir(parents=True, exist_ok=True)
                restored = a.src
                if os.path.lexists(a.src):
                    restored = library.unique_destination(a.src.parent, a.src.name)
                _move(a.dest, restored)
        except OSError as e:
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

    # ------------------------------------------------------------------ copy index (worker thread only)

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

    # ------------------------------------------------------------------ queue management

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

    def active_dests(self) -> frozenset:
        """Destinations of sorts this session can still undo (history uses Undo for these)."""
        with self.lock:
            return frozenset(str(a.dest) for a in self.history if a.dest)

    def add_next(self, path: Path) -> Track:
        """Queue a file as the next track (e.g. one put back from the history)."""
        with self.lock:
            t = self._new_track(path)
            self.queue.insert(min(1, len(self.queue)), t)
            self.total += 1
            return t

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
                    hay = library.relative_posix(t.path, self.music_root).lower()
                    return all(w in hay for w in words)

                indexed = [(i, t) for i, t in indexed if hit(t)]
            page = indexed[offset : offset + limit]
            items = [dict(self._track_public(t), index=i) for i, t in page]
            return {"total": len(indexed), "offset": offset, "items": items, "query": " ".join(words)}

    # ------------------------------------------------------------------ stats

    def folder_count(self, preset: dict, slot: int) -> int:
        """Audio files currently in a slot's destination folder (today's folder when dated)."""
        s = self._settings()
        d = library.slot_destination_dir(
            self.music_root, preset, slot, dated=bool(s.get("create_dated_sorting_sessions")), today=self._today()
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
            }

    def _track_public(self, t: Track) -> dict:
        return {
            "id": t.id,
            "name": t.path.name,
            "folder": library.source_folder_display(t.path, self.music_root),
        }
