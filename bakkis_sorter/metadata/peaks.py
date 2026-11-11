"""SQLite waveform peak storage and cache."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from .cache import content_digest
from .reader import _cache_key

MAX_PEAKS_LEN = 8192

class PeakStore:
    """Waveform peaks store (raw 800 bytes BLOB in SQLite) with transparent Base64 and legacy disk migration."""

    def __init__(self, directory: Path, *, storage: Optional[Any] = None) -> None:
        self.directory = Path(directory)
        self._storage = storage

    @property
    def storage(self) -> Any:
        if self._storage is None:
            from ..storage import get_storage

            self._storage = get_storage()
        return self._storage

    def _digest(self, path: Path) -> Optional[str]:
        return content_digest(path)

    def _legacy_digest(self, path: Path) -> Optional[str]:
        key = _cache_key(path)
        if key is None:
            return None
        return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()

    def _file(self, digest: str) -> Path:
        return self.directory / digest[:2] / f"{digest}.json"

    def get_bytes(self, path: Path) -> Optional[bytes]:
        digest = self._digest(path)
        if digest is None:
            return None

        # 1. Check SQLite waveform_peaks with content digest
        try:
            cached = self.storage.get_peaks_bytes(digest)
            if cached:
                return cached
        except (sqlite3.Error, OSError):
            pass

        # 2. Check legacy path-dependent digest & migrate if found
        leg_digest = self._legacy_digest(path)
        if leg_digest and leg_digest != digest:
            try:
                leg_cached = self.storage.get_peaks_bytes(leg_digest)
                if leg_cached:
                    self.storage.put_peaks_bytes(digest, leg_cached)
                    return leg_cached
            except (sqlite3.Error, OSError):
                pass

        # 3. Check legacy disk JSON file & migrate on hit
        for d in (digest, leg_digest):
            if not d:
                continue
            f = self._file(d)
            if f.is_file():
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                    peaks_b64 = data.get("peaks") if isinstance(data, dict) else None
                    if isinstance(peaks_b64, str):
                        try:
                            raw = base64.b64decode(peaks_b64)
                            self.storage.put_peaks_bytes(digest, raw)
                            f.unlink(missing_ok=True)
                            return raw
                        except (sqlite3.Error, OSError, ValueError):
                            pass
                except (OSError, ValueError):
                    pass
        return None

    def put_bytes(self, path: Path, peaks: bytes | memoryview) -> bool:
        if not isinstance(peaks, (bytes, memoryview)) or len(peaks) > MAX_PEAKS_LEN:
            return False
        digest = self._digest(path)
        if digest is None:
            return False
        try:
            self.storage.put_peaks_bytes(digest, bytes(peaks))
            self._file(digest).unlink(missing_ok=True)
            leg = self._legacy_digest(path)
            if leg:
                self._file(leg).unlink(missing_ok=True)
            return True
        except (sqlite3.Error, OSError):
            return False

    def get(self, path: Path) -> Optional[str]:
        raw = self.get_bytes(path)
        return base64.b64encode(raw).decode("ascii") if raw is not None else None

    def put(self, path: Path, peaks: str) -> bool:
        if not isinstance(peaks, str) or len(peaks) > MAX_PEAKS_LEN:
            return False
        try:
            raw = base64.b64decode(peaks, validate=True)
        except ValueError:
            return False
        return self.put_bytes(path, raw)
