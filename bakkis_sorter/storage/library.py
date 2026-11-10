"""Storage mixin for copy mode index, session history journal, and key-value state."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .crypto import CryptoError


class LibraryStorageMixin:
    """Table operations for copy_index, session_history, and kv_store."""

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT ciphertext FROM kv_store WHERE key = ?",
                (str(key),),
            ).fetchone()
        if not row:
            return default
        try:
            return self.cipher.decrypt_json(row[0])
        except (CryptoError, json.JSONDecodeError, ValueError):
            return default

    def put_kv(self, key: str, value: Any) -> None:
        ct = self.cipher.encrypt_json(value)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO kv_store (key, ciphertext, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    updated_at = excluded.updated_at
                """,
                (str(key), ct, now),
            )
            self._conn.commit()

    def has_copied(self, src_path: Path | str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM copy_index WHERE src_path = ?",
                (str(src_path),),
            ).fetchone()
        return row is not None

    def record_copied(self, src_path: Path | str, dest_path: Path | str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO copy_index (src_path, dest_path, copied_at)
                VALUES (?, ?, ?)
                ON CONFLICT(src_path) DO UPDATE SET
                    dest_path = excluded.dest_path,
                    copied_at = excluded.copied_at
                """,
                (str(src_path), str(dest_path), now),
            )
            self._conn.commit()

    def remove_copied(self, src_path: Path | str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM copy_index WHERE src_path = ?", (str(src_path),))
            self._conn.commit()

    def get_copied_set(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT src_path FROM copy_index").fetchall()
        return {str(r[0]) for r in rows}

    def log_journal_entry(self, entry: dict) -> None:
        event = str(entry.get("event") or "sort")
        t = float(entry.get("t") or time.time())
        mode = entry.get("mode")
        op = entry.get("op")
        src = entry.get("src")
        dest = entry.get("dest")
        label = entry.get("label")
        preset = entry.get("preset")
        sess_id = entry.get("session_id")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO session_history (event, action_time, mode, op, src_path, dest_path, label, preset_name, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event,
                    t,
                    str(mode) if mode is not None else None,
                    str(op) if op is not None else None,
                    str(src) if src is not None else None,
                    str(dest) if dest is not None else None,
                    str(label) if label is not None else None,
                    str(preset) if preset is not None else None,
                    str(sess_id) if sess_id is not None else None,
                ),
            )
            self._conn.commit()

    def get_journal_entries(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event, action_time, mode, op, src_path, dest_path, label, preset_name, session_id
                FROM session_history
                ORDER BY action_time DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "event": r[0],
                    "t": r[1],
                    "mode": r[2],
                    "op": r[3],
                    "src": r[4],
                    "dest": r[5],
                    "label": r[6],
                    "preset": r[7],
                    "session_id": r[8],
                }
            )
        return out
