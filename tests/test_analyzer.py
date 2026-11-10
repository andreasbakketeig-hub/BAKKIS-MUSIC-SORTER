"""Unit tests for the audio analysis engine (libsonare)."""

import secrets
from pathlib import Path
import tempfile
import pytest

pytestmark = pytest.mark.audio

from bakkis_sorter.analyzer import (
    TrackAnalysis,
    analyze_audio_file,
    normalize_dj_bpm,
    to_camelot,
)
from bakkis_sorter.metadata import read_meta
from bakkis_sorter.storage import EncryptedStorage
from tests.audio_fixtures import make_wav, convert, HAS_AFCONVERT


def test_to_camelot_mapping():
    assert to_camelot("A minor") == "8A"
    assert to_camelot("Am") == "8A"
    assert to_camelot("a minor") == "8A"
    assert to_camelot("C major") == "8B"
    assert to_camelot("c") == "8B"
    assert to_camelot("F# minor") == "11A"
    assert to_camelot("Gb major") == "2B"
    assert to_camelot("8A") == "8A"
    assert to_camelot("11B") == "11B"
    assert to_camelot(None) is None
    assert to_camelot("") is None
    assert to_camelot("unknown_nonexistent_key") is None


def test_normalize_dj_bpm():
    assert normalize_dj_bpm(126.0) == 126.0
    assert normalize_dj_bpm(63.0) == 126.0  # folded up
    assert normalize_dj_bpm(256.0) == 128.0  # folded down
    assert normalize_dj_bpm(0.0) == 126.0  # zero fallback
    assert normalize_dj_bpm(-10.0) == 126.0  # negative fallback
    assert normalize_dj_bpm(174.5) == 174.5


def test_analyze_audio_file_missing_file():
    analysis = analyze_audio_file(Path("/nonexistent/path/track.mp3"))
    assert analysis.bpm == 126.0
    assert analysis.jump_delta == 15.24


def test_analyze_audio_file_wav(tmp_path):
    wav = make_wav(tmp_path / "test.wav", seconds=2.0, rate=44100)
    analysis = analyze_audio_file(wav)
    assert isinstance(analysis, TrackAnalysis)
    assert 80.0 <= analysis.bpm <= 180.0
    assert analysis.jump_delta > 0.0


@pytest.mark.skipif(not HAS_AFCONVERT, reason="afconvert required for AIFF test")
def test_analyze_audio_file_aiff(tmp_path):
    wav = make_wav(tmp_path / "test.wav", seconds=2.0, rate=44100)
    aiff = convert(wav, tmp_path / "test.aiff", "AIFF", "BEI16@44100")
    analysis = analyze_audio_file(aiff)
    assert isinstance(analysis, TrackAnalysis)
    assert 80.0 <= analysis.bpm <= 180.0
    assert analysis.jump_delta > 0.0


def test_storage_track_analysis_cache(tmp_path):
    st = EncryptedStorage(tmp_path / "test.db", master_key=secrets.token_bytes(32))

    # Empty lookup
    assert st.get_analysis("sha1_abc123") is None

    # Put and get
    st.put_analysis(
        file_hash="sha1_abc123",
        bpm=128.0,
        musical_key="A minor",
        camelot_key="8A",
        drop_seconds=64.25,
        jump_delta=15.0,
        confidence=0.92,
    )

    rec = st.get_analysis("sha1_abc123")
    assert rec is not None
    assert rec["bpm"] == 128.0
    assert rec["musical_key"] == "A minor"
    assert rec["camelot_key"] == "8A"
    assert rec["drop_seconds"] == 64.25
    assert rec["jump_delta"] == 15.0
    assert rec["confidence"] == 0.92
    assert rec["analyzed_at"] > 0

    st.close()


def test_read_meta_populates_camelot_and_jump_delta(tmp_path):
    wav = make_wav(tmp_path / "test_meta.wav", seconds=1.0, rate=44100)
    meta = read_meta(wav)
    assert meta.jump_delta > 0.0
    assert hasattr(meta, "camelot")
    assert hasattr(meta, "drop_seconds")
