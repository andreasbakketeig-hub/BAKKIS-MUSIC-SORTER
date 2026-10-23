"""Backend facade exposed to the web UI as ``POST /api/<method>``.

Every public API method is named ``api_<method>`` and takes keyword params from the JSON body.
Native window actions (folder dialog, fullscreen, reveal in Finder) are added by ``app.py``.
"""

from __future__ import annotations

import inspect
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from . import __version__, library
from .config import MODES, PresetError, PresetStore, config_dir, load_settings, norm_mode, save_settings
from .metadata import MetadataCache, PeakStore
from .server import ApiError
from .session import Session, SessionError, filter_already_copied

PREFETCH_AHEAD = 8


class AppBackend:
    def __init__(self, *, presets: Optional[PresetStore] = None, on_change: Optional[Callable[[], None]] = None) -> None:
        self.lock = threading.RLock()
        self.settings = load_settings()
        self.presets = presets or PresetStore()
        self.meta = MetadataCache()
        self.peaks = PeakStore(config_dir() / "peaks")
        self.session: Optional[Session] = None
        self._scan_job: Optional[library.ScanJob] = None
        self.scan = {"status": "idle", "root": None, "count": 0, "error": None}
        self._on_change = on_change
        self._native: dict[str, Callable[..., object]] = {}

    # ------------------------------------------------------------------ plumbing

    def register_native(self, name: str, fn: Callable[..., object]) -> None:
        """Let app.py add window-level calls (e.g. ``choose_folder``)."""
        self._native[name] = fn

    def dispatch(self, method: str, params: dict) -> object:
        fn = self._native.get(method) or getattr(self, f"api_{method}", None)
        if fn is None or not callable(fn):
            raise ApiError(f"Unknown method: {method}", 404)
        try:
            inspect.signature(fn).bind(**params)
        except TypeError as e:
            raise ApiError(f"Bad parameters for {method}: {e}") from e
        try:
            return fn(**params)
        except (SessionError, PresetError) as e:
            raise ApiError(str(e), 409) from e

    def audio_path(self, track_id: str) -> Optional[Path]:
        s = self.session
        return s.track_path(track_id) if s else None

    def cover(self, track_id: str) -> Optional[bytes]:
        p = self.audio_path(track_id)
        return self.meta.cover(p) if p else None

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                pass

    def _get_settings(self) -> dict:
        with self.lock:
            return dict(self.settings)

    def _prefetch(self) -> None:
        s = self.session
        if s is None:
            return
        with s.lock:
            paths = [t.path for t in s.queue[: PREFETCH_AHEAD + 1]]
        self.meta.prefetch(paths)

    def close(self) -> None:
        with self.lock:
            if self._scan_job:
                self._scan_job.cancel()
            if self.session:
                self.session.close()
        self.meta.shutdown()

    # ------------------------------------------------------------------ state

    def api_state(self) -> dict:
        with self.lock:
            snap = self.session.snapshot() if self.session else None
            return {
                "version": __version__,
                "settings": dict(self.settings),
                "presets": self.presets.data,
                "scan": dict(self.scan),
                "session": snap,
                "default_folder_exists": self._default_folder_exists(),
            }

    def _default_folder_exists(self) -> bool:
        p = self.settings.get("default_folder_path")
        return bool(p) and Path(p).expanduser().is_dir()

    def api_notices(self, since: int = 0) -> list:
        s = self.session
        return s.notices_since(int(since)) if s else []

    def api_track_meta(self, track_id: str) -> dict:
        p = self.audio_path(track_id)
        if p is None:
            raise ApiError("Unknown track", 404)
        return {"track_id": track_id, **self.meta.get(p).to_dict()}

    def api_peaks_get(self, track_id: str) -> Optional[str]:
        p = self.audio_path(track_id)
        return self.peaks.get(p) if p else None

    def api_peaks_put(self, track_id: str, peaks: str) -> bool:
        p = self.audio_path(track_id)
        return self.peaks.put(p, peaks) if p else False

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
            job = library.ScanJob(
                root,
                recursive=bool(s["include_subfolders"]),
                on_progress=lambda n: self._scan_progress(job, n),
                on_done=lambda paths: self._scan_done(job, root, paths),
                on_error=lambda e: self._scan_error(job, e),
            )
            self._scan_job = job
        job.start()
        self._changed()
        return dict(self.scan)

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

    def _scan_done(self, job: library.ScanJob, root: Path, paths: list) -> None:
        with self.lock:
            if job is not self._scan_job:
                return
            s = self.settings
            if s["file_operation"] == "copy" and s["hide_copied_on_rescan"]:
                paths = filter_already_copied(paths, root)
            ordered = library.order_tracks(paths, s["track_order"])
            old = self.session
            self.session = Session(
                root, ordered, presets=self.presets, settings=self._get_settings, on_change=self._changed, lock=self.lock
            )
            self.scan.update(status="ready", count=len(ordered))
            self._scan_job = None
        if old is not None:
            # Let pending file ops of the previous folder finish without blocking the scan thread's caller.
            threading.Thread(target=old.close, daemon=True).start()
        self._prefetch()
        self._changed()

    # ------------------------------------------------------------------ sorting

    def _session(self) -> Session:
        s = self.session
        if s is None:
            raise ApiError("No folder loaded", 409)
        return s

    def _after_queue_change(self) -> dict:
        self._prefetch()
        self._changed()
        return self._session().snapshot()

    def api_sort(self, track_id: str, mode: str, slot: int) -> dict:
        self._session().sort(str(track_id), norm_mode(mode), int(slot))
        return self._after_queue_change()

    def api_skip(self, track_id: str, mode: str = "swipe") -> dict:
        self._session().skip(str(track_id), norm_mode(mode))
        return self._after_queue_change()

    def api_undo(self) -> dict:
        self._session().undo_last()
        return self._after_queue_change()

    def api_undo_all(self) -> dict:
        self._session().undo_all()
        return self._after_queue_change()

    def api_set_order(self, order: str) -> dict:
        if order not in ("random", "in_order"):
            raise ApiError("order must be 'random' or 'in_order'")
        self.api_set_settings(track_order=order)
        if self.session:
            self.session.set_order(order)
            return self._after_queue_change()
        return {}

    def api_play_next(self, track_id: str) -> dict:
        self._session().play_next(str(track_id))
        return self._after_queue_change()

    def api_queue(self, offset: int = 0, limit: int = 100) -> dict:
        s = self.session
        return s.queue_slice(offset, limit) if s else {"total": 0, "offset": 0, "items": []}

    def api_reveal(self, target: str = "sorted") -> str:
        """Open the BAKKIS_SORTED folder (or the music folder) in Finder."""
        s = self._session()
        path = library.sorted_root(s.music_root)
        if target != "sorted" or not path.is_dir():
            path = s.music_root
        self._open_in_finder(path)
        return str(path)

    def _open_in_finder(self, path: Path) -> None:
        subprocess.Popen(["open", str(path)])

    def api_folder_counts(self, mode: str) -> list:
        s = self._session()
        with self.lock:
            preset = dict(self.presets.active(norm_mode(mode)))
        return [s.folder_count(preset, i) for i in range(len(preset["labels"]))]

    # ------------------------------------------------------------------ settings

    def api_set_settings(self, **changes: object) -> dict:
        with self.lock:
            merged = dict(self.settings)
            merged.update(changes)
            self.settings = save_settings(merged)
            out = dict(self.settings)
        self._changed()
        return out

    # ------------------------------------------------------------------ presets

    def api_preset_select(self, preset_id: str) -> dict:
        with self.lock:
            p = self.presets.set_active(preset_id)
        self._changed()
        return p

    def api_preset_create(self, mode: str, name: str, labels: list, colors: list, folders: Optional[list] = None) -> dict:
        if norm_mode(mode) not in MODES:
            raise ApiError("Bad mode")
        with self.lock:
            p = self.presets.create(mode, name, labels, colors, folders)
        self._changed()
        return p

    def api_preset_update(self, preset_id: str, **fields: object) -> dict:
        with self.lock:
            p = self.presets.update(preset_id, **fields)
        self._changed()
        return p

    def api_preset_delete(self, preset_id: str) -> dict:
        with self.lock:
            self.presets.delete(preset_id)
            data = self.presets.data
        self._changed()
        return data

    def api_preset_reset_grid(self, preset_id: str) -> dict:
        with self.lock:
            p = self.presets.reset_grid_to_default_genres(preset_id)
        self._changed()
        return p
