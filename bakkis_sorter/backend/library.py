"""Local library scanning, sorting, undo, queue, and history API mixin for AppBackend."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import feedback, history, keys, library, playlists
from ..config import norm_mode, safe_segment, save_settings
from ..duplicates import DuplicateIndex
from ..metadata import MetadataCache
from ..server import ApiError
from ..session import Journal, Session, filter_already_copied

if TYPE_CHECKING:
    from ..config import PresetStore

SUPPORT_NUDGE_AT = 250  # tracks sorted, across sessions: one quiet "buy me a coffee" note
SAVE_TOTAL_EVERY = 10


class LibraryApiMixin:
    """API methods for folder scanning, sorting decisions, undo/queue manipulation, and session history."""

    if TYPE_CHECKING:
        lock: threading.RLock
        settings: dict
        presets: PresetStore
        meta: MetadataCache
        journal: Journal
        session: Optional[Session]
        dupes: Optional[DuplicateIndex]
        scan: dict
        _scan_job: Optional[library.ScanJob]
        _closed: bool

        def _changed(self) -> None: ...
        def _prefetch(self) -> None: ...
        def _get_settings(self) -> dict: ...
        def _on_session_change(self, s: Optional[Session] = None) -> None: ...
        def _copy_to_clipboard(self, text: str) -> None: ...
        def audio_path(self, track_id: str) -> Optional[Path]: ...
        def api_set_settings(self, **changes: object) -> dict: ...

    # ------------------------------------------------------------------ folder / scan

    def api_load_folder(self, path: str) -> dict:
        root = Path(path).expanduser()
        if not root.is_dir():
            raise ApiError(f"Folder not found: {path}", 404)
        root = root.resolve()
        with self.lock:
            if self._scan_job is not None:
                self._scan_job.cancel()
            s = self.settings
            self.scan = {"status": "scanning", "root": str(root), "count": 0, "error": None}
            # A second pass inside a sorted tree writes <preset>/… folders next to the tracks: never rescan them.
            root_skip = (
                frozenset(p["name"].lower() for p in self.presets.presets)
                | frozenset(safe_segment(p["name"]).lower() for p in self.presets.presets)
                if library.sorts_in_place(root)
                else frozenset()
            )
            old = self.session
            job = library.ScanJob(
                root,
                recursive=bool(s["include_subfolders"]),
                root_skip=root_skip,
                batch_size=25,
                # Pending moves of the current folder finish before scanning, so the scan sees where files are now.
                before=old.flush if old is not None else None,
                on_progress=lambda n: self._scan_progress(job, n),
                on_batch=lambda batch: self._scan_batch(job, root, batch),
                on_done=lambda paths: self._scan_done(job, root, paths),
                on_error=lambda e: self._scan_error(job, e),
            )
            self._scan_job = job
        job.start()
        self._changed()
        return dict(self.scan)

    def api_close_session(self) -> dict:
        """Close the active sorting or promo session and return to empty landing screen."""
        with self.lock:
            if self._scan_job is not None:
                self._scan_job.cancel()
                self._scan_job = None
            if self.session is not None:
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = None
            self.scan = None
            self._changed()
        return {"ok": True}

    def _scan_progress(self, job: library.ScanJob, n: int) -> None:
        with self.lock:
            if job is self._scan_job:
                self.scan["count"] = n
        self._changed()

    def _scan_error(self, job: library.ScanJob, e: Exception) -> None:
        with self.lock:
            if job is not self._scan_job:
                return
            self.scan.update(status="error", error=str(e))
        self._changed()

    def _scan_batch(self, job: library.ScanJob, root: Path, batch: list[Path]) -> None:
        old_to_close: Optional[Session] = None
        should_prefetch = False
        with self.lock:
            if job is not self._scan_job or self._closed:
                return
            s = self.settings
            if library.effective_operation(root, s["file_operation"]) == "copy" and s["hide_copied_on_rescan"]:
                batch = filter_already_copied(batch, root)
            if not batch:
                return
            ordered = library.order_tracks(batch, s["track_order"])
            if not getattr(job, "_session_started", False):
                job._session_started = True  # type: ignore[attr-defined]
                old_to_close = self.session
                if self.dupes is not None:
                    self.dupes.cancel()
                self.dupes = DuplicateIndex(root, self.meta.get).start()
                box: list = []
                self.session = Session(
                    root,
                    ordered,
                    presets=self.presets,
                    settings=self._get_settings,
                    journal=self.journal,
                    on_change=lambda: self._on_session_change(box[0] if box else None),
                    lock=self.lock,
                )
                box.append(self.session)
                self.scan.update(status="ready", count=len(ordered))
                should_prefetch = True
            else:
                self.session.append_tracks(ordered)
                self.scan["count"] = self.session.total
        if old_to_close is not None:
            try:
                old_to_close.close()
            except Exception:
                pass
        if should_prefetch:
            self._prefetch()
        self._changed()

    def _scan_done(self, job: library.ScanJob, root: Path, paths: list) -> None:
        old_to_close: Optional[Session] = None
        with self.lock:
            if job is not self._scan_job or self._closed:
                return
            if not getattr(job, "_session_started", False):
                job._session_started = True  # type: ignore[attr-defined]
                old_to_close = self.session
                if self.dupes is not None:
                    self.dupes.cancel()
                box: list = []
                self.session = Session(
                    root,
                    [],
                    presets=self.presets,
                    settings=self._get_settings,
                    journal=self.journal,
                    on_change=lambda: self._on_session_change(box[0] if box else None),
                    lock=self.lock,
                )
                box.append(self.session)
                self.scan.update(status="ready", count=0)
            else:
                self.scan.update(status="ready", count=self.session.total)
            self._scan_job = None
        if old_to_close is not None:
            try:
                old_to_close.close()
            except Exception:
                pass
        self._changed()

    # ------------------------------------------------------------------ sorting

    def _session(self) -> Session:
        s = self.session
        if s is None:
            raise ApiError("No folder loaded", 409)
        return s

    def _changing(self) -> Session:
        """The session for a call that moves files or changes the queue; refused while a folder loads."""
        if self.scan.get("status") == "scanning":
            raise ApiError("Still loading the folder", 409)
        return self._session()

    def _after_queue_change(self, s: Session) -> dict:
        """Snapshot of the session the call changed (not one a rescan swapped in meanwhile)."""
        self._prefetch()
        self._changed()
        return s.snapshot()

    def api_sort(self, track_id: str, mode: str, slot: int) -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.sort(str(track_id), norm_mode(mode), int(slot))
        nudge = self._count_sorted()
        out = self._after_queue_change(s)
        if nudge:
            import sys
            api_mod = sys.modules.get("bakkis_sorter.api")
            target = getattr(api_mod, "SUPPORT_NUDGE_AT", SUPPORT_NUDGE_AT)
            out = dict(out, support_nudge=target)
        return out

    def _count_sorted(self) -> bool:
        """Count a sort; True exactly once, when the total reaches SUPPORT_NUDGE_AT."""
        import sys
        api_mod = sys.modules.get("bakkis_sorter.api")
        target_nudge = getattr(api_mod, "SUPPORT_NUDGE_AT", SUPPORT_NUDGE_AT)
        save_every = getattr(api_mod, "SAVE_TOTAL_EVERY", SAVE_TOTAL_EVERY)
        with self.lock:
            s = self.settings
            s["sorted_total"] = s.get("sorted_total", 0) + 1
            nudge = s["sorted_total"] >= target_nudge and not s.get("support_nudged")
            if nudge:
                s["support_nudged"] = True
            if nudge or s["sorted_total"] % save_every == 0:
                self.settings = save_settings(s)
        return nudge

    def api_skip(self, track_id: str, mode: str = "swipe") -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.skip(str(track_id), norm_mode(mode))
        return self._after_queue_change(s)

    def api_undo(self) -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.undo_last()
        return self._after_queue_change(s)

    def api_undo_all(self) -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.undo_all()
        return self._after_queue_change(s)

    def api_set_order(self, order: str) -> dict:
        if order not in ("random", "in_order"):
            raise ApiError("order must be 'random' or 'in_order'")
        self.api_set_settings(track_order=order)
        s = self.session
        if s:
            s.set_order(order)
            return self._after_queue_change(s)
        return {}

    def api_play_next(self, track_id: str) -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.play_next(str(track_id))
        return self._after_queue_change(s)

    def api_queue(self, offset: int = 0, limit: int = 100, q: str = "") -> dict:
        s = self.session
        if s is None:
            return {"total": 0, "offset": 0, "items": []}
        page = s.queue_slice(offset, limit, q)
        if self.settings.get("queue_filter"):
            for item in page["items"]:  # BPM / key from the cache (filled by prefetch and filters)
                m = self.meta.peek(s.track_path(item["id"]) or Path())
                if m is not None:
                    item["bpm"], item["key"] = m.bpm, keys.to_camelot(m.key) or m.key
        return page

    def api_reveal_track(self, track_id: str) -> str:
        """Show a track's file selected in Finder."""
        p = self.audio_path(str(track_id))
        if p is None or not p.exists():
            raise ApiError("The file is not there any more", 404)
        subprocess.Popen(["open", "-R", str(p)])
        return str(p)

    def api_reveal(self, target: str = "sorted") -> str:
        """Open where this session sorts to (or its playlists / Discover folder) in Finder; else the music folder."""
        s = self._session()
        root = s.music_root
        if target == "sorted":
            first = library.output_base(root, s.operation())
            cands = [first] + [t for t in library.tree_roots(root) if t != first]
        elif target == "playlists":
            cands = [t / playlists.PLAYLIST_DIR for t in library.tree_roots(root)]
        elif target == "discover":
            from .. import listen
            cands = [library.copies_root(root) / listen.DISCOVER_DIR]
        else:
            cands = []
        path = next((c for c in cands if c.is_dir()), root)
        self._open_in_finder(path)
        return str(path)

    def _open_in_finder(self, path: Path) -> None:
        subprocess.Popen(["open", str(path)])

    # ------------------------------------------------------------------ history across sessions

    def _active_dests(self) -> frozenset:
        s = self.session
        return s.active_dests() if s else frozenset()

    def api_history(self) -> list:
        return history.load(self.journal, active_dests=self._active_dests())

    def api_history_restore(self, entry_ids: list) -> list:
        s = self.session
        if s:
            s.flush()  # never race the worker on the same files
        results = history.restore(self.journal, [str(x) for x in entry_ids], active_dests=self._active_dests())
        if s:
            for r in results:
                if r.get("ok") and r.get("restored"):
                    p = Path(r["restored"])
                    if p.exists() and str(p).startswith(str(s.music_root) + os.sep) and library.is_audio_path(p):
                        s.add_next(p)
        self._changed()
        return results

    def api_feedback(self, session_id: Optional[str] = None, copy: bool = True) -> dict:
        """Plain-text summary of a session's decisions; copied to the clipboard by default."""
        s = self.session
        if s is not None:
            s.flush()
        sid = session_id or (s.session_id if s else None)
        sessions = history.load(self.journal, active_dests=self._active_dests())
        sess = next((x for x in sessions if x["id"] == sid), None)
        res = feedback.build(sess["items"] if sess else [], self.meta.get, self.presets.presets)
        if copy and res["count"]:
            self._copy_to_clipboard(res["text"])
        return res

    def api_set_filter(
        self,
        bpm_min: Optional[float] = None,
        bpm_max: Optional[float] = None,
        key: Optional[str] = None,
        compatible: bool = False,
        order: str = "queue",
    ) -> dict:
        """Advanced: show only tracks in a BPM range and/or (harmonically compatible) key."""
        s = self._session()
        code = keys.to_camelot(key) if key else None
        if key and not code:
            raise ApiError(f"Unknown key: {key}")
        wanted = keys.compatible(code) if code and compatible else ({code} if code else None)
        pool = s.filter_pool()
        info = {}
        keep, ranked = set(), []
        for t in pool:  # metadata is cached; this reads tags only for tracks not seen yet
            m = self.meta.get(t.path)
            try:
                bpm = float(m.bpm) if m.bpm else None
            except ValueError:
                bpm = None
            camelot = keys.to_camelot(m.key)
            if bpm_min is not None and (bpm is None or bpm < float(bpm_min) - 0.5):
                continue
            if bpm_max is not None and (bpm is None or bpm > float(bpm_max) + 0.5):
                continue
            if wanted is not None and camelot not in wanted:
                continue
            keep.add(t.id)
            ranked.append((t.id, bpm, camelot))
        if order == "bpm":
            ranked.sort(key=lambda r: (r[1] is None, r[1] or 0))
        elif order == "key":
            ranked.sort(key=lambda r: (keys.sort_key(r[2]), r[1] or 0))
        info = {
            "bpm_min": bpm_min,
            "bpm_max": bpm_max,
            "key": code,
            "compatible": bool(compatible and code),
            "order": order,
        }
        s.apply_filter(keep, [r[0] for r in ranked] if order in ("bpm", "key") else None, info)
        return self._after_queue_change(s)

    def api_clear_filter(self) -> dict:
        with self.lock:  # same lock as load_folder: no sort slips in while a scan starts
            s = self._changing()
            s.clear_filter()
        return self._after_queue_change(s)

    def api_folder_counts(self, mode: str) -> list:
        s = self._session()
        with self.lock:
            preset = dict(self.presets.active(norm_mode(mode)))
        return [s.folder_count(preset, i) for i in range(len(preset["labels"]))]
