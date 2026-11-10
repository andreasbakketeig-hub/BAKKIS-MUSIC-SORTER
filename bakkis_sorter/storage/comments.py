"""Storage mixin for configurable promo comments editor in SQLite."""

from __future__ import annotations

import time


class CommentStorageMixin:
    """Table operations for promo_comments."""

    def get_promo_comments(self) -> dict[int, list[str]]:
        """Retrieve promo comments grouped by rating from the database, auto-migrating if empty."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT rating, comment FROM promo_comments ORDER BY id ASC"
            ).fetchall()

        pools: dict[int, list[str]] = {5: [], 4: []}
        for r_int, comment in rows:
            if r_int not in pools:
                pools[r_int] = []
            pools[r_int].append(comment)

        # Auto-migrate from default / file if completely empty
        if not pools[5] and not pools[4]:
            from ..promos.comments import DEFAULT_COMMENTS_TEXT, default_comments_path, parse_comments_text

            p = default_comments_path()
            try:
                txt = p.read_text(encoding="utf-8") if p.is_file() else DEFAULT_COMMENTS_TEXT
            except OSError:
                txt = DEFAULT_COMMENTS_TEXT
            initial_pools = parse_comments_text(txt)
            self.set_promo_comments(initial_pools)
            return initial_pools

        return pools

    def set_promo_comments(self, pools: dict[int, list[str]]) -> None:
        """Replace comments for the given ratings in promo_comments table."""
        from ..promos.comments import sanitize_comment

        now = time.time()
        with self._lock:
            for rating_key, comments in pools.items():
                try:
                    r_int = int(rating_key)
                except (ValueError, TypeError):
                    continue
                if r_int not in (1, 2, 3, 4, 5):
                    continue
                self._conn.execute("DELETE FROM promo_comments WHERE rating = ?", (r_int,))
                for c in comments:
                    clean = sanitize_comment(c)
                    if clean:
                        self._conn.execute(
                            "INSERT INTO promo_comments (rating, comment, updated_at) VALUES (?, ?, ?)",
                            (r_int, clean, now),
                        )
            self._conn.commit()
