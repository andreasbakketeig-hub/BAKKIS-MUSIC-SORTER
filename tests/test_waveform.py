import base64
import struct
import wave

import pytest

from bakkis_sorter import waveform

from .audio_fixtures import HAS_AFCONVERT, convert, make_mp3


def ramp_wav(path, width=2, channels=2, rate=8000, seconds=2):
    """Silence for the first half, then a full-scale square wave on the last channel."""
    n = rate * seconds
    full = (1 << (8 * width - 1)) - 1
    frames = bytearray()
    for i in range(n):
        v = 0 if i < n // 2 else (full if i % 2 else -full)
        for c in range(channels):
            s = v if c == channels - 1 else 0
            if width == 1:
                frames += bytes([s + 128 if s >= 0 else 128 + s])
            else:
                frames += s.to_bytes(width, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def check_shape(peaks):
    assert peaks is not None and len(peaks) == waveform.PEAKS
    half = len(peaks) // 2
    assert max(peaks[: half - 1]) == 0
    assert min(peaks[half + 1 :]) >= 250


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_wav_int_widths(tmp_path, width):
    check_shape(waveform.compute(ramp_wav(tmp_path / "a.wav", width=width)))


def test_wav_float(tmp_path):
    n = 8000
    data = b"".join(struct.pack("<f", 0.0 if i < n // 2 else 0.5) for i in range(n))
    fmt = struct.pack("<HHIIHH", 3, 1, 8000, 32000, 4, 32)
    riff = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    p = tmp_path / "f.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(riff)) + riff)
    check_shape(waveform.compute(p))


@pytest.mark.skipif(not HAS_AFCONVERT, reason="needs macOS afconvert")
@pytest.mark.parametrize("fmt, data", [("AIFF", "BEI16"), ("AIFF", "BEI24"), ("AIFC", "BEF32")])
def test_aiff_variants(tmp_path, fmt, data):
    src = ramp_wav(tmp_path / "a.wav")
    check_shape(waveform.compute(convert(src, tmp_path / "a.aiff", fmt, data)))


def test_compressed_and_broken_files_return_none(tmp_path):
    assert waveform.compute(make_mp3(tmp_path / "a.mp3")) is None
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF\x10\x00\x00\x00WAVEjunkjunk")
    assert waveform.compute(bad) is None
    assert waveform.compute(tmp_path / "missing.wav") is None
    assert waveform.compute_b64(bad) is None


def test_b64_and_long_bins_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(waveform, "MAX_READ_PER_BIN", 64)
    out = waveform.compute_b64(ramp_wav(tmp_path / "a.wav"))
    check_shape(base64.b64decode(out))


def test_is_pcm_path(tmp_path):
    assert waveform.is_pcm_path(tmp_path / "x.WAV") and waveform.is_pcm_path(tmp_path / "x.aif")
    assert not waveform.is_pcm_path(tmp_path / "x.flac")
