"""Waveform peaks for uncompressed audio (WAV, AIFF), computed in Python.

Decoding a 100 MB AIFF in the web view keeps hundreds of MB inside WebKit. PCM needs no decoding,
so the backend reads the samples straight from the file instead. Only the most significant byte of
each integer sample is looked at: plenty for a 0–255 waveform, and fast with the stdlib alone.
Compressed formats return None and the UI decodes them as before.
"""

from __future__ import annotations

import base64
import struct
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

PEAKS = 800
MAX_READ_PER_BIN = 128 * 1024  # bytes; longer bins are sampled from their start


@dataclass
class PcmInfo:
    offset: int  # first sample byte
    length: int  # bytes of sample data
    channels: int
    width: int  # bytes per sample
    kind: str  # "int", "uint8" or "float"
    big_endian: bool


def _wav_info(f: BinaryIO, size: int) -> Optional[PcmInfo]:
    fmt = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return None
        cid, clen = head[:4], struct.unpack("<I", head[4:])[0]
        if cid == b"fmt ":
            raw = f.read(min(clen, 40))
            if len(raw) < 16:
                return None
            tag, channels, _rate, _bps, _align, bits = struct.unpack("<HHIIHH", raw[:16])
            if tag == 0xFFFE and len(raw) >= 26:
                tag = struct.unpack("<H", raw[24:26])[0]  # first bytes of the SubFormat GUID
            fmt = (tag, channels, bits)
        elif cid == b"data" and fmt:
            tag, channels, bits = fmt
            width = (bits + 7) // 8
            if tag == 1 and width in (1, 2, 3, 4):
                kind = "uint8" if width == 1 else "int"
            elif tag == 3 and width == 4:
                kind = "float"
            else:
                return None
            length = min(clen, size - pos - 8)
            return PcmInfo(pos + 8, length, channels, width, kind, False) if channels else None
        pos += 8 + clen + (clen & 1)
    return None


def _aiff_info(f: BinaryIO, size: int, aifc: bool) -> Optional[PcmInfo]:
    comm = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return None
        cid, clen = head[:4], struct.unpack(">I", head[4:])[0]
        if cid == b"COMM":
            raw = f.read(min(clen, 64))
            if len(raw) < 18:
                return None
            channels, _frames, bits = struct.unpack(">HIH", raw[:8])
            comp = raw[18:22] if aifc and len(raw) >= 22 else b"NONE"
            comm = (channels, bits, comp)
        elif cid == b"SSND" and comm:
            channels, bits, comp = comm
            width = (bits + 7) // 8
            raw = f.read(8)
            if len(raw) < 8:
                return None
            data_offset = struct.unpack(">I", raw[:4])[0]
            start = pos + 16 + data_offset
            length = min(clen - 8 - data_offset, size - start)
            if comp in (b"NONE", b"twos") and width in (1, 2, 3, 4):
                return PcmInfo(start, length, channels, width, "int", True)
            if comp == b"sowt" and width in (2, 3, 4):
                return PcmInfo(start, length, channels, width, "int", False)
            if comp in (b"fl32", b"FL32") and width == 4:
                return PcmInfo(start, length, channels, width, "float", True)
            return None
        pos += 8 + clen + (clen & 1)
    return None


def pcm_info(f: BinaryIO, size: int) -> Optional[PcmInfo]:
    f.seek(0)
    head = f.read(12)
    if len(head) < 12:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        info = _wav_info(f, size)
    elif head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        info = _aiff_info(f, size, head[8:12] == b"AIFC")
    else:
        return None
    return info if info and info.length > 0 else None


# Most significant byte -> absolute level 0..127
_ABS_INT8 = bytes(min(127, abs(b - 256 if b > 127 else b)) for b in range(256))
_ABS_UINT8 = bytes(min(127, abs(b - 128)) for b in range(256))


def _block_peak(data: bytes, info: PcmInfo) -> float:
    """Largest absolute sample in ``data``, scaled to 0..1."""
    if info.kind == "float":
        a = array("f")
        a.frombytes(data[: len(data) // 4 * 4])
        if info.big_endian != (sys.byteorder == "big"):
            a.byteswap()
        if not a:
            return 0.0
        v = max(max(a), -min(a))
        return min(v, 1.0) if v == v else 0.0  # NaN guard
    msb = data[0 :: info.width] if info.big_endian else data[info.width - 1 :: info.width]
    level = msb.translate(_ABS_UINT8 if info.kind == "uint8" else _ABS_INT8)
    for v in range(127, 0, -1):  # loudest level first; each test is a C-speed byte search
        if v in level:
            return v / 127
    return 0.0


PCM_SUFFIXES = {".wav", ".aif", ".aiff"}


def is_pcm_path(path: Path) -> bool:
    """WAV/AIFF by extension: the UI must not decode these itself (see module docstring)."""
    return Path(path).suffix.lower() in PCM_SUFFIXES


def compute(path: Path, n: int = PEAKS) -> Optional[bytes]:
    """``n`` peaks (0–255, normalised to the loudest) or None if the file is not plain PCM."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            info = pcm_info(f, size)
            if info is None:
                return None
            frame = info.channels * info.width
            frames = info.length // frame
            if frames < n:
                return None
            per = frames // n  # frames per bin
            want = min(per * frame, MAX_READ_PER_BIN // frame * frame)
            out = []
            for i in range(n):
                f.seek(info.offset + i * per * frame)
                out.append(_block_peak(f.read(want), info))
    except OSError:
        return None
    top = max(out)
    return bytes(round(v / top * 255) if top else 0 for v in out)


def compute_b64(path: Path, n: int = PEAKS) -> Optional[str]:
    peaks = compute(path, n)
    return base64.b64encode(peaks).decode("ascii") if peaks is not None else None
