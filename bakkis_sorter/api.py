"""Backend facade exposed to the web UI as ``POST /api/<method>``.

Every public API method is named ``api_<method>`` and takes keyword params from the JSON body.
Native window actions (folder dialog, fullscreen, reveal in Finder) are added by ``app.py``.
"""

from __future__ import annotations

import inspect
import os
import platform
import subprocess
import threading
from urllib.parse import urlsplit
from pathlib import Path
from typing import Callable, Optional

from . import __version__, library
from .config import MODES, PresetError, PresetStore, config_dir, load_settings, norm_mode, repair_default_folder, safe_segment, save_settings
from . import feedback, history, keys, listen, playlists, updates, waveform
from .duplicates import DuplicateIndex
from .metadata import MetadataCache, PeakStore
from .server import ApiError
from .session import Journal, Session, SessionError, filter_already_copied

PREFETCH_AHEAD = 8
SUPPORT_NUDGE_AT = 250  # tracks sorted, across sessions: one quiet "buy me a coffee" note
SAVE_TOTAL_EVERY = 10


class AppBackend:
    def __init__(self, *, presets: Optional[PresetStore] = None, on_change: Optional[Callable[[], None]] = None) -> None:
        self.lock = threading.RLock()
        self.settings = load_settings()
        if repair_default_folder(self.settings):
            self.settings = save_settings(self.settings)
        # Presets saved before they had their own date switch follow the old global setting.
        self.presets = presets or PresetStore(dated_default=self.settings["create_dated_sorting_sessions"])
        self.meta = MetadataCache()
        self.journal = Journal()
        self.peaks = PeakStore(config_dir() / "peaks")
        self.session: Optional[Session] = None
        self.dupes: Optional[DuplicateIndex] = None
        self.listen: Optional[listen.ListenSession] = None
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
        s, ls = self.session, self.listen
        p = s.track_path(track_id) if s else None
        if p is None and ls is not None:
            p = ls.track_path(track_id)
        return p

    def cover(self, track_id: str) -> Optional[bytes]:
        p = self.audio_path(track_id)
        return self.meta.cover(p) if p else None

    def _on_session_change(self) -> None:
        """Called by the session after file ops: index newly sorted files for the duplicate check."""
        s, d = self.session, self.dupes
        if s is not None and d is not None:
            with s.lock:
                fresh = [a.dest for a in s.history[-3:] if a.status == "done" and a.dest]
            for path in fresh:
                d.add(path)
        self._changed()

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
            session = self.session
            try:
                save_settings(self.settings)  # keeps sorted_total exact
            except OSError:
                pass
        # Outside the lock: the session shares it, and its worker needs it to finish pending moves.
        if session:
            session.close()
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
                "home": str(Path.home()),
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
        meta = self.meta.get(p)
        dupes = self.dupes
        dup = dupes.find(p, meta) if dupes is not None else None
        return {"track_id": track_id, **meta.to_dict(), "duplicate": dup, "pcm": waveform.is_pcm_path(p)}

    def api_peaks_get(self, track_id: str) -> Optional[str]:
        """Cached peaks; WAV/AIFF are computed here so the web view never has to decode them."""
        p = self.audio_path(track_id)
        if not p:
            return None
        cached = self.peaks.get(p)
        if cached is None:
            cached = waveform.compute_b64(p)
            if cached is not None:
                self.peaks.put(p, cached)
        return cached

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
            # A second pass inside a sorted tree writes <preset>/… folders next to the tracks: never rescan them.
            root_skip = frozenset(p["name"].lower() for p in self.presets.presets) | frozenset(
                safe_segment(p["name"]).lower() for p in self.presets.presets
            ) if library.sorts_in_place(root) else frozenset()
            job = library.ScanJob(
                root,
                recursive=bool(s["include_subfolders"]),
                root_skip=root_skip,
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
            if library.effective_operation(root, s["file_operation"]) == "copy" and s["hide_copied_on_rescan"]:
                paths = filter_already_copied(paths, root)
            ordered = library.order_tracks(paths, s["track_order"])
            old = self.session
            if self.dupes is not None:
                self.dupes.cancel()
            self.dupes = DuplicateIndex(root, self.meta.get).start()
            self.session = Session(
                root,
                ordered,
                presets=self.presets,
                settings=self._get_settings,
                journal=self.journal,
                on_change=self._on_session_change,
                lock=self.lock,
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
        nudge = self._count_sorted()
        out = self._after_queue_change()
        if nudge:
            out = dict(out, support_nudge=SUPPORT_NUDGE_AT)
        return out

    def _count_sorted(self) -> bool:
        """Count a sort; True exactly once, when the total reaches SUPPORT_NUDGE_AT."""
        with self.lock:
            s = self.settings
            s["sorted_total"] = s.get("sorted_total", 0) + 1
            nudge = s["sorted_total"] >= SUPPORT_NUDGE_AT and not s.get("support_nudged")
            if nudge:
                s["support_nudged"] = True
            if nudge or s["sorted_total"] % SAVE_TOTAL_EVERY == 0:
                self.settings = save_settings(s)
        return nudge

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

    def api_queue(self, offset: int = 0, limit: int = 100, q: str = "") -> dict:
        s = self.session
        if s is None:
            return {"total": 0, "offset": 0, "items": []}
        page = s.queue_slice(offset, limit, q)
        if self.settings.get("advanced_features"):
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
            # A second pass sorts into the loaded folder itself; otherwise the tree of the current mode first.
            first = library.output_base(root, s.operation())
            cands = [first] + [t for t in library.tree_roots(root) if t != first]
        elif target == "playlists":
            cands = [t / playlists.PLAYLIST_DIR for t in library.tree_roots(root)]
        elif target == "discover":
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
        # Files put back inside the loaded folder join the queue as the next tracks.
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

    def _copy_to_clipboard(self, text: str) -> None:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)

    def api_open_url(self, url: str) -> bool:
        """Open a link in the user's browser / mail app. Only https: and mailto: links are allowed."""
        parts = urlsplit(str(url))
        if parts.scheme not in ("https", "mailto") or (parts.scheme == "https" and not parts.netloc):
            raise ApiError("Only https and mailto links can be opened")
        self._open_external(str(url))
        return True

    def _open_external(self, url: str) -> None:
        subprocess.Popen(["open", url])

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
        info = {"bpm_min": bpm_min, "bpm_max": bpm_max, "key": code, "compatible": bool(compatible and code), "order": order}
        s.apply_filter(keep, [r[0] for r in ranked] if order in ("bpm", "key") else None, info)
        return self._after_queue_change()

    def api_clear_filter(self) -> dict:
        self._session().clear_filter()
        return self._after_queue_change()

    # ------------------------------------------------------------------ listen mode (advanced; never moves files)

    def api_listen_sources(self) -> list:
        s = self._session()
        with s.lock:
            unsorted = len(s.queue)
        name = s.music_root.name
        out = [{"key": "queue", "label": f"Unsorted tracks in {name}", "count": unsorted}] if unsorted else []
        return out + listen.sorted_sources(s.music_root)

    def api_listen_start(self, sources: list, shuffle: bool = True) -> dict:
        s = self._session()
        labels = {x["key"]: x["label"] for x in self.api_listen_sources()}
        items: list = []
        for key in [str(k) for k in sources]:
            if key == "queue":
                with s.lock:
                    items += [(t.path, labels.get("queue", "Unsorted")) for t in s.queue]
            else:
                folder = listen.resolve_source(s.music_root, key)
                if folder is None:
                    raise ApiError(f"Unknown source: {key}")
                items += [(p, labels.get(key, folder.name)) for p in playlists._audio_files(folder)]  # noqa: SLF001
        if not items:
            raise ApiError("Nothing to listen to in the chosen folders", 409)
        self.listen = listen.ListenSession(items, shuffle=bool(shuffle))
        self.meta.prefetch([t.path for t in self.listen.tracks[:8]])
        hearted = {i["path"] for i in self._hearts().items}
        return {"total": len(self.listen.tracks), "tracks": self.listen.public(s.music_root, hearted)}

    def _hearts(self) -> listen.Hearts:
        if getattr(self, "_hearts_store", None) is None:
            self._hearts_store = listen.Hearts()
        return self._hearts_store

    def api_listen_heart(self, track_id: str, on: bool = True) -> dict:
        """Heart / un-heart the playing Discover track."""
        p = self.listen.track_path(track_id) if self.listen else None
        if p is None:
            raise ApiError("Not a Discover track", 404)
        source = self.listen.source_of.get(str(track_id), "") if self.listen else ""
        state = self._hearts().set(p, bool(on), source)
        return {"hearted": state, "count": len(self._hearts().items)}

    def api_hearts(self) -> list:
        return self._hearts().public(lambda p: listen.current_location(p, self.journal))

    def api_hearts_remove(self, path: str) -> int:
        self._hearts().set(Path(path), False)
        return len(self._hearts().items)

    def api_hearts_clear(self) -> bool:
        self._hearts().clear()
        return True

    def api_hearts_create_folder(self, name: Optional[str] = None, clear: bool = True) -> dict:
        s = self._session()
        res = listen.create_folder(s.music_root, self._hearts(), self.journal, name)
        if clear and res["copied"]:
            self._hearts().clear()
        return res

    def api_hearts_export_playlist(self) -> dict:
        s = self._session()
        items = [Path(i["current"]) for i in self.api_hearts() if i["current"]]
        return playlists.write_playlist(s.music_root, "Discover - Hearted", items, self.meta.get)

    def api_listen_prefetch(self, track_ids: list) -> bool:
        if self.listen:
            self.meta.prefetch([p for p in (self.listen.track_path(t) for t in track_ids) if p])
        return True

    def api_listen_stop(self) -> bool:
        self.listen = None
        return True

    def api_export_playlists(self) -> dict:
        """Advanced: M3U8 per destination folder into BAKKIS_SORTED/_Playlists."""
        s = self._session()
        s.flush()  # include sorts that are still being written
        return playlists.export(s.music_root, self.meta.get)

    def api_check_update(self) -> dict:
        """Ask GitHub Releases once per launch whether a newer version exists (only if enabled)."""
        if not self.settings.get("check_for_updates", True):
            return {"current": __version__, "error": "disabled"}
        if getattr(self, "_update_result", None) is None:
            self._update_result = updates.check()
        return self._update_result

    def api_about(self) -> dict:
        return {"version": __version__, "macos": platform.mac_ver()[0] or platform.platform(), "python": platform.python_version()}

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

    def api_preset_create(
        self, mode: str, name: str, labels: list, colors: list, folders: Optional[list] = None, dated: Optional[bool] = None
    ) -> dict:
        if norm_mode(mode) not in MODES:
            raise ApiError("Bad mode")
        with self.lock:
            if dated is None:
                dated = self.settings["create_dated_sorting_sessions"]
            p = self.presets.create(mode, name, labels, colors, folders, dated=bool(dated))
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
