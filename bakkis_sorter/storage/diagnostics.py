"""Storage mixin for structured app logging, SQLite database stats, and VACUUM defragmentation."""

from __future__ import annotations

import random
import sqlite3
import time
from typing import Any, Optional


class DiagnosticStorageMixin:
    """Table operations for app_logs, database statistics, and vacuum defragmentation."""

    def record_log(
        self,
        level: str,
        module: str,
        message: str,
        exception_type: Optional[str] = None,
        traceback: Optional[str] = None,
        context: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """Insert a log record and prune older entries to keep storage compact."""
        now = timestamp if timestamp is not None else time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO app_logs (created_at, level, module, message, exception_type, traceback, context)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    str(level).upper(),
                    str(module),
                    str(message),
                    str(exception_type) if exception_type is not None else None,
                    str(traceback) if traceback is not None else None,
                    str(context) if context is not None else None,
                ),
            )
            # Automatic rolling truncation: keep max 2,000 entries or within last 30 days
            if random.random() < 0.02:
                cutoff = now - (30 * 86400)
                self._conn.execute(
                    """
                    DELETE FROM app_logs WHERE created_at < ?
                    OR id NOT IN (SELECT id FROM app_logs ORDER BY created_at DESC LIMIT 2000)
                    """,
                    (cutoff,),
                )
            self._conn.commit()

    def get_logs(
        self,
        min_level: str = "INFO",
        limit: int = 100,
        offset: int = 0,
        module: Optional[str] = None,
        level: Optional[str] = None,
    ) -> list[dict]:
        """Fetch structured log entries ordered newest first."""
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        params: list[Any] = []

        if level and level.upper() in levels:
            query = """
                SELECT id, created_at, level, module, message, exception_type, traceback, context
                FROM app_logs
                WHERE level = ?
            """
            params.append(level.upper())
        elif min_level.upper() == "ALL":
            query = """
                SELECT id, created_at, level, module, message, exception_type, traceback, context
                FROM app_logs
                WHERE 1=1
            """
        else:
            min_idx = levels.index(min_level.upper()) if min_level.upper() in levels else 1
            allowed_levels = levels[min_idx:]
            placeholders = ",".join("?" for _ in allowed_levels)
            query = f"""
                SELECT id, created_at, level, module, message, exception_type, traceback, context
                FROM app_logs
                WHERE level IN ({placeholders})
            """
            params.extend(allowed_levels)

        if module:
            query += " AND module = ?"
            params.append(str(module))
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        out = []
        for r in rows:
            out.append(
                {
                    "id": r[0],
                    "created_at": r[1],
                    "level": r[2],
                    "module": r[3],
                    "message": r[4],
                    "exception_type": r[5],
                    "traceback": r[6],
                    "context": r[7],
                }
            )
        return out

    def get_database_stats(self) -> dict:
        """Return file sizes, SQLite freelist statistics, and table row counts."""
        with self._lock:
            file_size = self.db_path.stat().st_size if self.db_path.is_file() else 0
            wal_path = self.db_path.with_name(self.db_path.name + "-wal")
            wal_size = wal_path.stat().st_size if wal_path.is_file() else 0

            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0] or 4096
            page_count = self._conn.execute("PRAGMA page_count").fetchone()[0] or 0
            freelist_count = self._conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
            reclaimable_bytes = freelist_count * page_size

            counts: dict[str, int] = {}
            for tbl in (
                "app_logs",
                "waveform_peaks",
                "session_history",
                "copy_index",
                "promo_reviews",
                "offline_promo_reviews",
                "promo_track_signatures",
                "track_analysis",
                "promo_cache",
                "kv_store",
            ):
                try:
                    row = self._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                    counts[tbl] = row[0] if row else 0
                except (sqlite3.OperationalError, sqlite3.Error):
                    counts[tbl] = 0

        return {
            "file_size_bytes": file_size,
            "wal_size_bytes": wal_size,
            "total_size_bytes": file_size + wal_size,
            "reclaimable_bytes": reclaimable_bytes,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "counts": counts,
            "db_path": str(self.db_path),
        }

    def vacuum(self) -> dict:
        """Defragment bakkis.db, optimize search indexes, and reclaim disk space."""
        with self._lock:
            before_size = self.db_path.stat().st_size if self.db_path.is_file() else 0
            try:
                self._conn.execute("PRAGMA optimize")
                self._conn.execute("VACUUM")
            except (sqlite3.OperationalError, sqlite3.Error) as exc:
                raise RuntimeError(f"Database vacuum failed: {exc}") from exc
            after_size = self.db_path.stat().st_size if self.db_path.is_file() else 0
            freelist_after = self._conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
            page_size = self._conn.execute("PRAGMA page_size").fetchone()[0] or 4096

        return {
            "before_bytes": before_size,
            "after_bytes": after_size,
            "reclaimed_bytes": max(0, before_size - after_size),
            "reclaimable_bytes": freelist_after * page_size,
        }

    def clear_logs(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM app_logs")
            self._conn.commit()
