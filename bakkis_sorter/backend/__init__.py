"""Backend facade exposed to the web UI as ``POST /api/<method>``.

Composed via single-responsibility domain mixins:
- PromoApiMixin: Inflyte + Trackstack promo bridges & offline stash
- LibraryApiMixin: Local folder scanning, sorting, undo, queue, and history
- SettingsApiMixin: Settings, presets, updater, and Listen mode
- DiagnosticsApiMixin: SQLite logs, database stats, and system diagnostics
"""

from __future__ import annotations

import inspect
from pathlib import Path
import threading
from typing import Callable, Optional

from .. import inflyte, library, storage, trackstack, updater
from ..config import PresetError, PresetStore, config_dir, load_settings, repair_default_folder, save_settings
from ..duplicates import DuplicateIndex
from ..metadata import MetadataCache, PeakStore
from ..pipeline import WarmupEngine
from ..server import ApiError
from ..session import Journal, Session, SessionError

from .diagnostics import DiagnosticsApiMixin
from .library import LibraryApiMixin, SAVE_TOTAL_EVERY, SUPPORT_NUDGE_AT
from .promos import PromoApiMixin
from .settings import OPEN_URL_HOSTS, OPEN_URL_MAIL, SettingsApiMixin

PREFETCH_AHEAD = 8


class AppBackend(
    PromoApiMixin,
    LibraryApiMixin,
    SettingsApiMixin,
    DiagnosticsApiMixin,
):
    """Unified application backend router composed of domain mixins."""

    def __init__(
        self,
        *,
        presets: Optional[PresetStore] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self.lock = threading.RLock()
        self.settings = load_settings()
        if repair_default_folder(self.settings):
            self.settings = save_settings(self.settings)
        # Presets saved before they had their own date switch follow the old global setting.
        self.presets = presets or PresetStore(dated_default=self.settings["create_dated_sorting_sessions"])
        self.meta = MetadataCache()
        self.journal = Journal()
        self.peaks = PeakStore(config_dir() / "peaks")
        self.storage = storage.get_storage()
        self.warmup = WarmupEngine(self.meta, self.storage)
        self.inflyte_bridge = inflyte.InflyteBridge(storage=self.storage)
        self.trackstack_bridge = trackstack.TrackstackBridge(storage=self.storage)
        self.comment_pool = inflyte.CommentPool()
        self.session: Optional[Session] = None
        self.dupes: Optional[DuplicateIndex] = None
        self.listen = None
        self._scan_job: Optional[library.ScanJob] = None
        self.scan = {"status": "idle", "root": None, "count": 0, "error": None}
        self._on_change = on_change
        self._native: dict[str, Callable[..., object]] = {}
        self._closed = False
        self.updater = updater.Installer(updater.app_bundle())
        self._update_result = None
        self._hearts_store = None

    # ------------------------------------------------------------------ plumbing

    def register_native(self, name: str, fn: Callable[..., object]) -> None:
        """Let app.py add window-level calls (e.g. ``choose_folder``)."""
        self._native[name] = fn

    def dispatch(self, method: str, params: dict) -> object:
        if self._closed:
            raise ApiError("The app is closing", 503)
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
        s, ls = self.session, self.listen
        p = s.track_path(track_id) if s else None
        if p is None and ls is not None:
            p = ls.track_path(track_id)
        return p

    def cover(self, track_id: str) -> Optional[bytes]:
        if getattr(self.session, "is_inflyte", False):
            return self.session.cover(track_id)  # type: ignore[attr-defined]
        p = self.audio_path(track_id)
        return self.meta.cover(p) if p else None

    def _on_session_change(self, s: Optional[Session] = None) -> None:
        """Called by a session after file ops: index newly sorted files for the duplicate check."""
        d = self.dupes
        if s is not None and s is self.session and d is not None:
            with s.lock:
                fresh = [a.dest for a in s.history[-3:] if a.status == "done" and a.dest]
            for path in fresh:
                d.add(path)
        self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    def _get_settings(self) -> dict:
        with self.lock:
            return dict(self.settings)

    def _prefetch(self) -> None:
        s = self.session
        if s is None or getattr(s, "is_inflyte", False):
            return
        with s.lock:
            paths = [t.path for t in s.queue[:50]]
        self.warmup.update_queue(paths)

    def close(self) -> None:
        with self.lock:
            self._closed = True
            if self._scan_job:
                self._scan_job.cancel()
                self._scan_job = None
            session = self.session
            try:
                save_settings(self.settings)
            except OSError:
                pass
        if hasattr(self, "warmup"):
            self.warmup.shutdown()
        if session:
            session.close()
        self.meta.shutdown()


__all__ = [
    "AppBackend",
    "DiagnosticsApiMixin",
    "LibraryApiMixin",
    "OPEN_URL_HOSTS",
    "OPEN_URL_MAIL",
    "PREFETCH_AHEAD",
    "PromoApiMixin",
    "SAVE_TOTAL_EVERY",
    "SUPPORT_NUDGE_AT",
    "SettingsApiMixin",
]
