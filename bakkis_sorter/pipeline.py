"""Unified 4-pillar lookahead warmup pipeline for audio tracks.

Warms up the next 50 upcoming tracks in sorting and discovery queues:
1. Metadata (mutagen ID3/FLAC/MP4 tags)
2. Album Artwork (downsampled 320px JPEG thumbnails in ByteBoundedLRU)
3. Waveform Peaks (800-byte raw BLOB in SQLite waveform_peaks)
4. Audio DSP Analysis (libsonare BPM, Camelot harmonic key, and first drop in SQLite track_analysis)

Runs on a low-priority thread pool pinned to efficiency cores so the main
UI thread and audio playback have zero latency and 120 FPS rendering.
"""

from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from . import waveform
from .analyzer import analyze_audio_file
from .logger import get_logger
from .metadata import MetadataCache, content_digest, file_digest
from .platform import set_background_thread_qos

logger = get_logger("pipeline")

LOOKAHEAD_WINDOW = 50
TIER1_WINDOW = 8


def _worker_init() -> None:
    set_background_thread_qos()


class WarmupEngine:
    """Unified 4-pillar lookahead warmup pipeline split into Fast I/O and low-priority DSP."""

    def __init__(
        self,
        metadata_cache: MetadataCache,
        storage: Optional[Any] = None,
        workers: int = 3,
        lookahead: int = LOOKAHEAD_WINDOW,
        dsp_workers: int = 1,
    ) -> None:
        self.meta = metadata_cache
        self._storage = storage
        self.lookahead = lookahead
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="bakkis-warmup-io",
            initializer=_worker_init,
        )
        self._dsp_queue: queue.PriorityQueue[tuple[tuple[int, int, int], Optional[Path]]] = queue.PriorityQueue()
        self._seq = itertools.count(1)
        self._generation = 0
        self._closed = False
        self._io_inflight: set[str] = set()
        self._dsp_inflight: set[str] = set()
        self._dsp_running: set[str] = set()
        self._dsp_queued_gen: dict[str, int] = {}
        self._inflight: set[str] = set()
        self._current_window: list[Path] = []
        self._window_set: set[str] = set()
        self._dsp_threads: list[threading.Thread] = []
        for idx in range(max(1, int(dsp_workers))):
            t = threading.Thread(
                target=self._dsp_worker_loop,
                daemon=True,
                name=f"bakkis-warmup-dsp-{idx}",
            )
            t.start()
            self._dsp_threads.append(t)

    @property
    def storage(self) -> Any:
        if self._storage is None:
            from .storage import get_storage

            self._storage = get_storage()
        return self._storage

    def update_queue(self, paths: list[Path]) -> None:
        """Update active lookahead window and schedule Fast I/O + prioritized DSP warmup."""
        window = [Path(p) for p in paths[: self.lookahead]]
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            gen = self._generation
            self._current_window = window
            self._window_set = {str(p) for p in window}

        for i, p in enumerate(window):
            tier1 = i < TIER1_WINDOW
            self._schedule_io(p, tier1)
            self._schedule_dsp(p, i, gen)

    def _schedule_io(self, path: Path, tier1: bool) -> None:
        p_str = str(path)
        with self._lock:
            if self._closed or p_str in self._io_inflight:
                return
            self._io_inflight.add(p_str)
            self._inflight.add(p_str)
        try:
            self._pool.submit(self._warm_io, path, tier1)
        except RuntimeError:
            with self._lock:
                self._io_inflight.discard(p_str)
                if p_str not in self._dsp_inflight:
                    self._inflight.discard(p_str)

    def _schedule_dsp(self, path: Path, index: int, gen: int) -> None:
        p_str = str(path)
        with self._lock:
            if self._closed or p_str in self._dsp_running:
                return
            if self._dsp_queued_gen.get(p_str, 0) >= gen:
                return
            self._dsp_queued_gen[p_str] = gen
            self._dsp_inflight.add(p_str)
            self._inflight.add(p_str)
        self._dsp_queue.put(((-gen, index, next(self._seq)), path))

    def _finish_io(self, p_str: str) -> None:
        with self._lock:
            self._io_inflight.discard(p_str)
            if p_str not in self._dsp_inflight:
                self._inflight.discard(p_str)

    def _finish_dsp(self, p_str: str) -> None:
        with self._lock:
            self._dsp_running.discard(p_str)
            self._dsp_inflight.discard(p_str)
            self._dsp_queued_gen.pop(p_str, None)
            if p_str not in self._io_inflight:
                self._inflight.discard(p_str)

    def _warm_io(self, path: Path, tier1: bool) -> None:
        p_str = str(path)
        try:
            # Pillar 1 & 2: Metadata and 320px Cover Thumbnail (<2ms Mutagen + Pillow)
            self.meta.get(path)
            self.meta.cover(path)

            # Pillar 3: PCM Waveform Peaks BLOB in SQLite
            st = self.storage
            if st is not None:
                try:
                    digest = content_digest(path)
                    if digest and not st.get_peaks_bytes(digest):
                        raw = waveform.compute(path)
                        if raw is not None:
                            st.put_peaks_bytes(digest, raw)
                except Exception as exc:
                    logger.debug("Waveform warmup error for %s: %s", path.name, exc)
        finally:
            self._finish_io(p_str)

    def _dsp_worker_loop(self) -> None:
        _worker_init()
        while True:
            prio, path = self._dsp_queue.get()
            if path is None:
                return
            p_str = str(path)
            gen = -prio[0]
            with self._lock:
                if self._closed:
                    self._finish_dsp(p_str)
                    return
                latest_gen = self._dsp_queued_gen.get(p_str, 0)
                if gen < latest_gen:
                    # A newer update_queue() promoted this track in the PriorityQueue.
                    continue
                if p_str not in self._window_set or p_str in self._dsp_running:
                    if p_str not in self._dsp_running:
                        self._dsp_inflight.discard(p_str)
                        self._dsp_queued_gen.pop(p_str, None)
                        if p_str not in self._io_inflight:
                            self._inflight.discard(p_str)
                    continue
                self._dsp_running.add(p_str)

            try:
                self._warm_dsp(path)
            finally:
                self._finish_dsp(p_str)

    def _warm_dsp(self, path: Path) -> None:
        """Pillar 4: Compute libsonare DSP analysis if still missing and update SQLite + RAM cache."""
        if not path.is_file():
            return
        st = self.storage
        if st is None:
            return
        try:
            f_hash = file_digest(path)
            if not f_hash:
                return
            cached = st.get_analysis(f_hash)
            if cached:
                self.meta.update_analysis(path, cached)
                return
            analysis = analyze_audio_file(path)
            st.put_analysis(
                file_hash=f_hash,
                bpm=analysis.bpm,
                musical_key=analysis.musical_key,
                camelot_key=analysis.camelot_key,
                drop_seconds=analysis.drop_seconds,
                jump_delta=analysis.jump_delta,
                confidence=analysis.confidence,
            )
            self.meta.update_analysis(
                path,
                {
                    "bpm": analysis.bpm,
                    "musical_key": analysis.musical_key,
                    "camelot_key": analysis.camelot_key,
                    "drop_seconds": analysis.drop_seconds,
                    "jump_delta": analysis.jump_delta,
                    "confidence": analysis.confidence,
                },
            )
        except Exception as exc:
            logger.debug("DSP analysis warmup error for %s: %s", path.name, exc)

    def shutdown(self) -> None:
        """Cancel queued warmups and shutdown I/O and DSP worker pools."""
        with self._lock:
            self._closed = True
            self._window_set.clear()
            self._dsp_queued_gen.clear()
            self._io_inflight.clear()
            self._dsp_inflight.clear()
            self._inflight.clear()
        while True:
            try:
                self._dsp_queue.get_nowait()
            except queue.Empty:
                break
        for _ in self._dsp_threads:
            self._dsp_queue.put(((999999, 999999, next(self._seq)), None))
        self._pool.shutdown(wait=False, cancel_futures=True)
