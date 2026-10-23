"""Generate tiny real audio files for metadata tests (stdlib wave + macOS afconvert)."""

import shutil
import struct
import subprocess
import wave
from pathlib import Path

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

HAS_AFCONVERT = shutil.which("afconvert") is not None


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return path


def make_mp3(path: Path, frames: int = 40) -> Path:
    """Silent MPEG-1 Layer III frames (128 kbps, 44.1 kHz, 417 bytes each)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    path.write_bytes(frame * frames)
    return path


def convert(wav: Path, dest: Path, fmt: str, data: str) -> Path:
    subprocess.run(["afconvert", "-f", fmt, "-d", data, str(wav), str(dest)], check=True, capture_output=True)
    return dest
