"""Storage mixin for promo track deduplication signatures."""

from __future__ import annotations

import time
from typing import Optional


class SignatureStorageMixin:
    """Table operations for promo_track_signatures."""

    def record_track_signature(
        self,
        signature: str,
        artist: str,
        title: str,
        decision: str,
        rating: Optional[int] = None,
        source: str = "inflyte",
        promo_name: str = "",
        reviewed_at: Optional[float] = None,
    ) -> None:
        """Record a normalized track signature (artist:title) and decision for duplicate warnings."""
        if not signature:
            return
        now = reviewed_at if reviewed_at is not None else time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO promo_track_signatures (signature, artist, title, decision, rating, source, promo_name, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signature) DO UPDATE SET
                    decision = excluded.decision,
                    rating = excluded.rating,
                    source = excluded.source,
                    promo_name = excluded.promo_name,
                    reviewed_at = excluded.reviewed_at
                """,
                (str(signature), str(artist), str(title), str(decision), rating, str(source), str(promo_name), now),
            )
            self._conn.commit()

    def get_track_signature_match(self, signature: Optional[str]) -> Optional[dict]:
        """Check if a track with this signature has already been reviewed; returns decision info if matched."""
        if not signature:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT artist, title, decision, rating, source, promo_name, reviewed_at
                FROM promo_track_signatures
                WHERE signature = ?
                """,
                (str(signature),),
            ).fetchone()
        if not row:
            return None
        return {
            "artist": row[0],
            "title": row[1],
            "decision": row[2],
            "rating": row[3],
            "source": row[4],
            "promo_name": row[5],
            "reviewed_at": row[6],
        }

    def prune_track_signatures(self, retention_days: int) -> int:
        """Remove promo signatures older than retention_days (0 = keep forever)."""
        if int(retention_days) <= 0:
            return 0
        cutoff = time.time() - (int(retention_days) * 86400)
        with self._lock:
            cur = self._conn.execute("DELETE FROM promo_track_signatures WHERE reviewed_at < ?", (cutoff,))
            pruned = cur.rowcount
            self._conn.commit()
        return pruned
