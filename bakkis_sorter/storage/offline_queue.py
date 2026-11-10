"""Storage mixin for offline promo review queue, stashed promos, and promo cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


class OfflineQueueStorageMixin:
    """Table operations for offline_promo_reviews, promo_cache, and stashed promo bundles."""

    def get_promo_cache(self, source: str, promo_key: str | int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT ciphertext FROM promo_cache WHERE source = ? AND promo_key = ?",
                (str(source), str(promo_key)),
            ).fetchone()
        if not row:
            return None
        try:
            val = self.cipher.decrypt_json(row[0])
            return val if isinstance(val, dict) else None
        except Exception:
            return None

    def put_promo_cache(self, source: str, promo_key: str | int, payload: dict) -> None:
        ct = self.cipher.encrypt_json(payload)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO promo_cache (source, promo_key, ciphertext, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source, promo_key) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    updated_at = excluded.updated_at
                """,
                (str(source), str(promo_key), ct, now),
            )
            self._conn.commit()

    def delete_promo_cache(self, source: str, promo_key: str | int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM promo_cache WHERE source = ? AND promo_key = ?",
                (str(source), str(promo_key)),
            )
            self._conn.commit()

    def queue_offline_review(
        self,
        source: str,
        promo_key: str | int,
        release_id: int,
        decision: str,
        rating: Optional[int] = None,
        comment: Optional[str] = None,
        favourite_track_id: Optional[int] = None,
        promo_name: str = "",
        payload: Optional[dict] = None,
        media_paths: Optional[list[Path]] = None,
    ) -> int:
        """Queue a promo rating/dismissal decision locally while offline."""
        now = time.time()
        ct_payload = self.cipher.encrypt_json(payload or {})
        paths_raw = [str(p) for p in (media_paths or [])]
        ct_paths = json.dumps(paths_raw)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO offline_promo_reviews (
                    source, promo_key, release_id, decision, rating, comment,
                    favourite_track_id, promo_name, payload_json, media_paths_json, queued_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                """,
                (
                    str(source),
                    str(promo_key),
                    int(release_id),
                    str(decision),
                    rating,
                    comment,
                    favourite_track_id,
                    str(promo_name),
                    ct_payload,
                    ct_paths,
                    now,
                ),
            )
            self._conn.commit()
            return cur.lastrowid or 0

    def get_pending_offline_reviews(self) -> list[dict]:
        """Fetch all queued offline reviews in chronological FIFO order."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, source, promo_key, release_id, decision, rating, comment,
                       favourite_track_id, promo_name, payload_json, media_paths_json, queued_at, status, error_message
                FROM offline_promo_reviews
                WHERE status = 'queued'
                ORDER BY queued_at ASC
                """
            ).fetchall()
        result = []
        for r in rows:
            try:
                payload = self.cipher.decrypt_json(r[9]) if r[9] else {}
            except Exception:
                payload = {}
            try:
                paths = json.loads(r[10]) if r[10] else []
            except Exception:
                paths = []
            result.append(
                {
                    "id": r[0],
                    "source": r[1],
                    "promo_key": r[2],
                    "release_id": r[3],
                    "decision": r[4],
                    "rating": r[5],
                    "comment": r[6],
                    "favourite_track_id": r[7],
                    "promo_name": r[8],
                    "payload": payload,
                    "media_paths": [Path(p) for p in paths],
                    "queued_at": r[11],
                    "status": r[12],
                    "error_message": r[13],
                }
            )
        return result

    def get_offline_review_count(self) -> int:
        """Count how many promo reviews are pending sync in offline mode."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM offline_promo_reviews WHERE status = 'queued'"
            ).fetchone()
        return int(row[0]) if row else 0

    def mark_offline_review_synced(self, review_id: int) -> None:
        """Transfer a synced review to promo_reviews, purge temporary cached files, and remove from queue."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT source, promo_key, decision, payload_json, media_paths_json
                FROM offline_promo_reviews
                WHERE id = ?
                """,
                (int(review_id),),
            ).fetchone()
            if not row:
                return
            source, promo_key, decision, ct_payload, paths_json = row
            try:
                details = self.cipher.decrypt_json(ct_payload) if ct_payload else {}
            except Exception:
                details = {}
            try:
                media_paths = [Path(p) for p in json.loads(paths_json)] if paths_json else []
            except Exception:
                media_paths = []

            self._conn.execute("DELETE FROM offline_promo_reviews WHERE id = ?", (int(review_id),))
            self._conn.commit()

        # record_review is on self via ReviewStorageMixin
        self.record_review(  # type: ignore[attr-defined]
            source=source,
            promo_key=promo_key,
            decision=decision,
            details=details,
            media_paths=media_paths,
        )

    def mark_offline_review_failed(self, review_id: int, error_message: str) -> None:
        """Flag an offline review that failed online submission with error details."""
        with self._lock:
            self._conn.execute(
                "UPDATE offline_promo_reviews SET status = 'failed', error_message = ? WHERE id = ?",
                (str(error_message), int(review_id)),
            )
            self._conn.commit()

    def get_all_stashed_promos(self) -> list[dict]:
        """Fetch all encrypted promo cache entries as deserialized dictionaries."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, promo_key, ciphertext FROM promo_cache ORDER BY updated_at DESC"
            ).fetchall()
        promos = []
        for src, _pkey, ct in rows:
            try:
                payload = self.cipher.decrypt_json(ct)
                if isinstance(payload, dict):
                    payload["source"] = src
                    promos.append(payload)
            except Exception:
                pass
        return promos
