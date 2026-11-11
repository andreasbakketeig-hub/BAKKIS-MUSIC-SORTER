"""Storage mixin for promo review tracking and decrypted review history."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional


def cleanup_media_files(paths: list[Optional[Path]]) -> None:
    """Remove temporary preview audio/cover files from the cache dir after a promo is reviewed."""
    for p in paths:
        if p is None:
            continue
        try:
            path_obj = Path(p)
            if path_obj.is_file():
                path_obj.unlink()
        except OSError:
            pass


class ReviewStorageMixin:
    """Table operations for promo_reviews."""

    def record_review(
        self,
        source: str,
        promo_key: str | int,
        decision: str,
        details: Optional[dict] = None,
        media_paths: Optional[list[Path]] = None,
    ) -> None:
        """Record a promo decision (rate_5, rate_4, support, dismiss, decline) and purge preview files."""
        ct = self.cipher.encrypt_json(details or {})
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO promo_reviews (source, promo_key, decision, ciphertext, reviewed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, promo_key) DO UPDATE SET
                    decision = excluded.decision,
                    ciphertext = excluded.ciphertext,
                    reviewed_at = excluded.reviewed_at
                """,
                (str(source), str(promo_key), str(decision), ct, now),
            )
            self._conn.execute(
                "DELETE FROM promo_cache WHERE source = ? AND promo_key = ?",
                (str(source), str(promo_key)),
            )
            self._conn.commit()

        if media_paths:
            cleanup_media_files(media_paths)

    def is_reviewed(self, source: str, promo_key: str | int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM promo_reviews WHERE source = ? AND promo_key = ?",
                (str(source), str(promo_key)),
            ).fetchone()
        return row is not None

    def get_reviewed_keys(self, source: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT promo_key FROM promo_reviews WHERE source = ?",
                (str(source),),
            ).fetchall()
        return {str(r[0]) for r in rows}

    def get_promo_review_history(
        self,
        limit: int = 100,
        offset: int = 0,
        source: Optional[str] = None,
        decision: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """Retrieve decrypted promo reviews ordered by reviewed_at DESC with optional filtering."""
        with self._lock:
            query = "SELECT source, promo_key, decision, ciphertext, reviewed_at FROM promo_reviews"
            conditions = []
            params: list = []
            if source and source.strip().lower() not in ("all", ""):
                conditions.append("source = ?")
                params.append(source.strip().lower())
            if decision and decision.strip().lower() not in ("all", ""):
                conditions.append("decision = ?")
                params.append(decision.strip().lower())
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY reviewed_at DESC"
            rows = self._conn.execute(query, tuple(params)).fetchall()

            # Optional signature cache to populate artist/title for older reviews
            sig_rows = self._conn.execute(
                "SELECT promo_name, artist, title FROM promo_track_signatures WHERE promo_name != ''"
            ).fetchall()
            promo_sig_map: dict[str, tuple[str, str]] = {r[0]: (r[1], r[2]) for r in sig_rows}

        decrypted_items: list[dict] = []
        search_term = (search or "").strip().lower()

        for r_source, r_key, r_dec, ct, r_at in rows:
            details: dict = {}
            if ct:
                try:
                    dec = self.cipher.decrypt_json(ct)
                    if isinstance(dec, dict):
                        details = dec
                except Exception:
                    details = {}

            promo_name = details.get("promo_name") or ""
            artist = details.get("artist") or ""
            title = details.get("title") or ""
            if (not artist or not title) and promo_name in promo_sig_map:
                sig_artist, sig_title = promo_sig_map[promo_name]
                if not artist:
                    artist = sig_artist
                if not title:
                    title = sig_title

            rating = details.get("rating")
            comment = details.get("comment") or ""
            downloaded = details.get("downloaded") or []

            if search_term:
                searchable = f"{promo_name} {artist} {title} {comment} {r_source} {r_dec}".lower()
                if search_term not in searchable:
                    continue

            decrypted_items.append(
                {
                    "source": r_source,
                    "promo_key": r_key,
                    "decision": r_dec,
                    "promo_name": promo_name,
                    "artist": artist,
                    "title": title,
                    "rating": rating,
                    "comment": comment,
                    "downloaded": downloaded,
                    "reviewed_at": r_at,
                }
            )

        total = len(decrypted_items)
        paginated = decrypted_items[offset : offset + limit] if limit > 0 else decrypted_items[offset:]
        return paginated, total
