"""Multi-threaded background metadata prefetching and bounded LRU cache."""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Optional

from .cover import (
    COVER_MAX_DIM,
    COVER_THUMB_QUALITY,
    MAX_COVER_BYTES,
    downsample_cover,
    image_mime,
    read_cover,
)
from .reader import _cache_key, read_meta
from .tags import TrackMeta

META_CACHE_SIZE = 2000
COVER_CACHE_ITEMS = 128
COVER_CACHE_BYTES = 8 * 1024 * 1024
COVER_CACHE_SIZE = COVER_CACHE_ITEMS
PREFETCH_WORKERS = 3

def _cache_key(path: Path) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_size, st.st_mtime_ns)


class _LRU:
    def __init__(self, size: int) -> None:
        self.size = size
        self._d: "OrderedDict[tuple, object]" = OrderedDict()

    def get(self, key: tuple):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def __contains__(self, key: tuple) -> bool:
        return key in self._d

    def put(self, key: tuple, value: object) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)


class ByteBoundedLRU:
    """LRU cache bounded by both maximum item count and total memory bytes."""

    def __init__(
        self,
        max_items: int = COVER_CACHE_ITEMS,
        max_bytes: int = COVER_CACHE_BYTES,
    ) -> None:
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._d: OrderedDict[Any, Any] = OrderedDict()
        self._sizes: dict[Any, int] = {}
        self.current_bytes: int = 0

    def get(self, key: Any) -> Optional[Any]:
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def __contains__(self, key: Any) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def put(self, key: Any, value: Any, byte_size: Optional[int] = None) -> None:
        size = (
            byte_size
            if byte_size is not None
            else (
                len(value)
                if isinstance(value, (bytes, bytearray, memoryview, str))
                else 0
            )
        )
        if key in self._d:
            self.current_bytes -= self._sizes.pop(key, 0)

        self._d[key] = value
        self._sizes[key] = size
        self.current_bytes += size
        self._d.move_to_end(key)

        while (
            len(self._d) > self.max_items or self.current_bytes > self.max_bytes
        ) and self._d:
            oldest_key, _ = self._d.popitem(last=False)
            self.current_bytes -= self._sizes.pop(oldest_key, 0)

    def clear(self) -> None:
        self._d.clear()
        self._sizes.clear()
        self.current_bytes = 0


_MISSING = object()


class MetadataCache:
    def __init__(self, workers: int = PREFETCH_WORKERS) -> None:
        self._lock = threading.Lock()
        self._meta = _LRU(META_CACHE_SIZE)
        self._covers = ByteBoundedLRU(max_items=COVER_CACHE_ITEMS, max_bytes=COVER_CACHE_BYTES)
        self._inflight: set = set()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bakkis-meta")

    def get(self, path: Path) -> TrackMeta:
        key = _cache_key(path)
        if key is None:
            return TrackMeta(0.0, None, None, Path(path).name, None, None, False)
        with self._lock:
            hit = self._meta.get(key)
        if hit is not None:
            return hit  # type: ignore[return-value]
        meta = read_meta(path)
        with self._lock:
            self._meta.put(key, meta)
        return meta

    def peek(self, path: Path) -> Optional[TrackMeta]:
        """Cached metadata only (never reads the file)."""
        key = _cache_key(path)
        if key is None:
            return None
        with self._lock:
            return self._meta.get(key)  # type: ignore[return-value]

    def cover(self, path: Path) -> Optional[bytes]:
        key = _cache_key(path)
        if key is None:
            return None
        with self._lock:
            hit = self._covers.get(key)
        if hit is not None:
            return None if hit is _MISSING else hit  # type: ignore[return-value]
        raw = read_cover(path)
        thumb = downsample_cover(raw) if raw is not None else None
        with self._lock:
            self._covers.put(key, thumb if thumb is not None else _MISSING)
        return thumb

    def prefetch(self, paths: Iterable[Path], covers: int = 8) -> None:
        """Read metadata for ``paths`` in the background; also covers for the first ``covers`` paths."""
        for i, p in enumerate(paths):
            key = _cache_key(p)
            if key is None:
                continue
            with self._lock:
                if key in self._meta or key in self._inflight:
                    continue
                self._inflight.add(key)
            self._pool.submit(self._prefetch_one, Path(p), key, i < covers)

    def _prefetch_one(self, path: Path, key: tuple, with_cover: bool) -> None:
        try:
            self.get(path)
            if with_cover:
                self.cover(path)
        finally:
            with self._lock:
                self._inflight.discard(key)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)  # queued prefetches must not delay quitting


# --------------------------------------------------------------------------- waveform peaks (waveform.py or the UI)

MAX_PEAKS_LEN = 8192


def content_digest(path: Path) -> Optional[str]:
    """Fast, path-independent audio content hash based on file size + head/tail 16KB sample chunks."""
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return None
        with open(path, "rb") as f:
            head = f.read(16384)
            tail = b""
            if st.st_size > 32768:
                f.seek(-16384, os.SEEK_END)
                tail = f.read(16384)
        hasher = hashlib.sha1()
        hasher.update(f"{st.st_size}:".encode("ascii"))
        hasher.update(head)
        if tail:
            hasher.update(tail)
        return hasher.hexdigest()
    except OSError:
        return None


