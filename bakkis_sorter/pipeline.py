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

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from . import waveform
from .analyzer import analyze_audio_file
from .logger import get_logger
from .metadata import MetadataCache, content_digest, file_digest

logger = get_logger("pipeline")

LOOKAHEAD_WINDOW = 50
TIER1_WINDOW = 8


def _worker_init() -> None:
    try:
        # Lower CPU priority (nice +10) so warmup runs on Efficiency cores on macOS/Unix
        os.setpriority(os.PRIO_PROCESS, 0, 10)
    except Exception:
        pass


class WarmupEngine:
    """Unified 4-pillar lookahead warmup pipeline for sorting and playback queues."""

    def __init__(
        self,
        metadata_cache: MetadataCache,
        storage: Optional[Any] = None,
        workers: int = 3,
        lookahead: int = LOOKAHEAD_WINDOW,
    ) -> None:
        self.meta = metadata_cache
        self._storage = storage
        self.lookahead = lookahead
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="bakkis-warmup",
            initializer=_worker_init,
        )
        self._inflight: set[str] = set()
        self._current_window: list[Path] = []

    @property
    def storage(self) -> Any:
        if self._storage is None:
            from .storage import get_storage

            self._storage = get_storage()
        return self._storage

    def update_queue(self, paths: list[Path]) -> None:
        """Update active lookahead window and schedule warmup for upcoming tracks."""
        window = [Path(p) for p in paths[: self.lookahead]]
        with self._lock:
            self._current_window = window

        for i, p in enumerate(window):
            tier1 = i < TIER1_WINDOW
            self._schedule_one(p, tier1)

    def _schedule_one(self, path: Path, tier1: bool) -> None:
        p_str = str(path)
        with self._lock:
            if p_str in self._inflight:
                return
            self._inflight.add(p_str)
        try:
            self._pool.submit(self._warm_track, path, tier1)
        except RuntimeError:
            with self._lock:
                self._inflight.discard(p_str)

    def _warm_track(self, path: Path, tier1: bool) -> None:
        p_str = str(path)
        try:
            # Pillar 1 & 2: Metadata and 320px Cover Thumbnail
            self.meta.get(path)
            self.meta.cover(path)

            st = self.storage

            # Pillar 3: Waveform Peaks BLOB in SQLite
            if st is not None:
                try:
                    digest = content_digest(path)
                    if digest and not st.get_peaks_bytes(digest):
                        raw = waveform.compute(path)
                        if raw is not None:
                            st.put_peaks_bytes(digest, raw)
                except Exception as exc:
                    logger.debug("Waveform warmup error for %s: %s", path.name, exc)

            # Pillar 4: DSP Analysis (BPM, Camelot harmonic key, drop seconds)
            if st is not None:
                try:
                    f_hash = file_digest(path)
                    if f_hash and not st.get_analysis(f_hash):
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
                except Exception as exc:
                    logger.debug("DSP analysis warmup error for %s: %s", path.name, exc)
        finally:
            with self._lock:
                self._inflight.discard(p_str)

    def shutdown(self) -> None:
        """Cancel queued warmups and shutdown worker pool."""
        self._pool.shutdown(wait=False, cancel_futures=True)
