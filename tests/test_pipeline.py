import secrets
import time
from pathlib import Path

import pytest

from bakkis_sorter.metadata import MetadataCache, content_digest, file_digest
from bakkis_sorter.pipeline import LOOKAHEAD_WINDOW, WarmupEngine
from bakkis_sorter.storage import EncryptedStorage

from .audio_fixtures import make_wav

pytestmark = pytest.mark.pipeline


def test_warmup_engine_four_pillars(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    cache = MetadataCache(workers=2)

    engine = WarmupEngine(metadata_cache=cache, storage=store, workers=2, lookahead=50)

    try:
        p = make_wav(tmp_path / "test_track.wav", seconds=1)
        engine.update_queue([p])

        # Wait for the async worker pool to finish the task
        deadline = time.time() + 3.0
        while time.time() < deadline:
            with engine._lock:
                if not engine._inflight:
                    break
            time.sleep(0.02)

        # Pillar 1: Metadata in RAM
        meta = cache.peek(p)
        assert meta is not None

        # Pillar 3: Waveform peaks in SQLite BLOB
        digest = content_digest(p)
        assert digest is not None
        peaks = store.get_peaks_bytes(digest)
        assert peaks is not None
        assert len(peaks) > 0

        # Pillar 4: DSP Analysis in SQLite
        f_hash = file_digest(p)
        assert f_hash is not None
        analysis = store.get_analysis(f_hash)
        assert analysis is not None
        assert "bpm" in analysis
    finally:
        engine.shutdown()
        cache.shutdown()
        store.close()


def test_warmup_engine_window_shifting(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    cache = MetadataCache(workers=2)
    engine = WarmupEngine(metadata_cache=cache, storage=store, workers=2, lookahead=50)

    try:
        dummy_paths = [tmp_path / f"track_{i:03d}.wav" for i in range(60)]
        engine.update_queue(dummy_paths)

        with engine._lock:
            assert len(engine._current_window) == 50
            assert dummy_paths[0] in engine._current_window
            assert dummy_paths[49] in engine._current_window
            assert dummy_paths[50] not in engine._current_window

        # Shift the queue forward by 10 tracks
        engine.update_queue(dummy_paths[10:])

        with engine._lock:
            assert len(engine._current_window) == 50
            assert dummy_paths[10] in engine._current_window
            assert dummy_paths[50] in engine._current_window
            assert dummy_paths[59] in engine._current_window
            assert dummy_paths[0] not in engine._current_window
    finally:
        engine.shutdown()
        cache.shutdown()
        store.close()


def test_warmup_engine_skip_cached(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    cache = MetadataCache(workers=2)
    engine = WarmupEngine(metadata_cache=cache, storage=store, workers=2, lookahead=10)

    try:
        p = make_wav(tmp_path / "cached.wav", seconds=1)
        engine.update_queue([p])

        deadline = time.time() + 3.0
        while time.time() < deadline:
            with engine._lock:
                if not engine._inflight:
                    break
            time.sleep(0.02)

        # Re-running on already cached track returns immediately
        t0 = time.perf_counter()
        engine.update_queue([p])
        dt = (time.perf_counter() - t0) * 1000
        assert dt < 50.0  # instant non-blocking dispatch
    finally:
        engine.shutdown()
        cache.shutdown()
        store.close()


def test_warmup_engine_skips_evicted_dsp_tracks(tmp_path: Path, monkeypatch) -> None:
    import threading
    import bakkis_sorter.pipeline as pipeline_mod
    from bakkis_sorter.analyzer import TrackAnalysis

    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    cache = MetadataCache(workers=2)

    p1 = make_wav(tmp_path / "01_first.wav", seconds=1)
    p2 = make_wav(tmp_path / "02_evicted.wav", seconds=1)
    p3 = make_wav(tmp_path / "03_promoted.wav", seconds=1)

    entered_first = threading.Event()
    release_first = threading.Event()
    analyzed: list[str] = []

    def fake_analyze(path: Path) -> TrackAnalysis:
        name = Path(path).name
        analyzed.append(name)
        if name == "01_first.wav":
            entered_first.set()
            release_first.wait(timeout=3.0)
        return TrackAnalysis(bpm=126.0, musical_key="A minor", camelot_key="8A", drop_seconds=32.0, confidence=0.9)

    monkeypatch.setattr(pipeline_mod, "analyze_audio_file", fake_analyze)

    engine = WarmupEngine(metadata_cache=cache, storage=store, workers=2, lookahead=2)
    try:
        engine.update_queue([p1, p2])
        assert entered_first.wait(timeout=3.0)

        # While p1 is busy analyzing, shift the active window so p2 is evicted and p3 enters
        engine.update_queue([p3])
        release_first.set()

        deadline = time.time() + 4.0
        while time.time() < deadline:
            with engine._lock:
                if not engine._inflight:
                    break
            time.sleep(0.02)

        assert "01_first.wav" in analyzed
        assert "03_promoted.wav" in analyzed
        assert "02_evicted.wav" not in analyzed
    finally:
        release_first.set()
        engine.shutdown()
        cache.shutdown()
        store.close()
