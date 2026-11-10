"""Storage mixin for audio DSP analysis (libsonare BPM, key, drop) and waveform peaks."""

from __future__ import annotations

import base64
import sqlite3
import time
from typing import Optional

DEFAULT_MAX_PEAKS_ENTRIES = 100_000


class AnalysisStorageMixin:
    """Table operations for track_analysis and waveform_peaks."""

    max_waveform_entries: int = DEFAULT_MAX_PEAKS_ENTRIES
    _peaks_insert_counter: int = 0

    def get_peaks_bytes(self, file_hash: str) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT peaks FROM waveform_peaks WHERE file_hash = ?",
                (str(file_hash),),
            ).fetchone()
        if not row or row[0] is None:
            return None
        val = row[0]
        if isinstance(val, (bytes, memoryview)):
            return bytes(val)
        if isinstance(val, str):
            try:
                return base64.b64decode(val)
            except Exception:
                return None
        return None

    def put_peaks_bytes(self, file_hash: str, peaks: bytes | memoryview) -> None:
        now = time.time()
        blob = sqlite3.Binary(bytes(peaks))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO waveform_peaks (file_hash, peaks, cached_at)
                VALUES (?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    peaks = excluded.peaks,
                    cached_at = excluded.cached_at
                """,
                (str(file_hash), blob, now),
            )
            self._conn.commit()
            self._peaks_insert_counter += 1
            if self._peaks_insert_counter >= 1000:
                self._peaks_insert_counter = 0
                self._maybe_prune_peaks()

    def get_peaks(self, file_hash: str) -> Optional[str]:
        """Backwards-compatible facade: returns Base64-encoded string."""
        raw = self.get_peaks_bytes(file_hash)
        return base64.b64encode(raw).decode("ascii") if raw is not None else None

    def put_peaks(self, file_hash: str, peaks: Any) -> None:
        """Backwards-compatible facade: accepts bytes, Base64 str, or legacy objects and stores as BLOB."""
        if isinstance(peaks, (bytes, memoryview)):
            raw = bytes(peaks)
        elif isinstance(peaks, str):
            try:
                raw = base64.b64decode(peaks, validate=True)
            except Exception:
                raw = peaks.encode("utf-8")
        else:
            import json

            try:
                raw = json.dumps(peaks).encode("utf-8")
            except Exception:
                raw = str(peaks).encode("utf-8")
        self.put_peaks_bytes(file_hash, raw)

    def prune_peaks(self, max_entries: Optional[int] = None) -> int:
        """Evict oldest waveform peak entries exceeding max_entries."""
        limit = max_entries if max_entries is not None else self.max_waveform_entries
        with self._lock:
            count = self._conn.execute("SELECT count(*) FROM waveform_peaks").fetchone()[0]
            if count <= limit:
                return 0
            excess = count - limit
            cur = self._conn.execute(
                """
                DELETE FROM waveform_peaks
                WHERE file_hash IN (
                    SELECT file_hash FROM waveform_peaks
                    ORDER BY cached_at ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
            pruned = cur.rowcount
            self._conn.commit()
            return pruned

    def _maybe_prune_peaks(self) -> None:
        try:
            self.prune_peaks()
        except sqlite3.Error:
            pass

    def get_analysis(self, file_hash: str) -> Optional[dict]:
        """Fetch cached BPM, key, drop seconds, and jump delta for an audio file hash."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT bpm, musical_key, camelot_key, drop_seconds, jump_delta, confidence, analyzed_at
                FROM track_analysis
                WHERE file_hash = ?
                """,
                (str(file_hash),),
            ).fetchone()
        if not row:
            return None
        return {
            "bpm": row[0],
            "musical_key": row[1],
            "camelot_key": row[2],
            "drop_seconds": row[3],
            "jump_delta": row[4],
            "confidence": row[5],
            "analyzed_at": row[6],
        }

    def put_analysis(
        self,
        file_hash: str,
        bpm: float,
        musical_key: Optional[str] = None,
        camelot_key: Optional[str] = None,
        drop_seconds: Optional[float] = None,
        jump_delta: Optional[float] = None,
        confidence: float = 0.0,
    ) -> None:
        """Cache libsonare analysis results for an audio file hash."""
        now = time.time()
        delta = jump_delta if jump_delta is not None else round(32.0 * (60.0 / bpm), 2)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO track_analysis (file_hash, bpm, musical_key, camelot_key, drop_seconds, jump_delta, confidence, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    bpm = excluded.bpm,
                    musical_key = excluded.musical_key,
                    camelot_key = excluded.camelot_key,
                    drop_seconds = excluded.drop_seconds,
                    jump_delta = excluded.jump_delta,
                    confidence = excluded.confidence,
                    analyzed_at = excluded.analyzed_at
                """,
                (str(file_hash), float(bpm), musical_key, camelot_key, drop_seconds, delta, float(confidence), now),
            )
            self._conn.commit()
