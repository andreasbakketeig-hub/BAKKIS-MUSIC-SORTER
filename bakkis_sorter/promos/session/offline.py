"""Promo session mixin for offline review synchronization and replay."""

from __future__ import annotations

from ...session import SessionError
from ..models import InflytePromo


class SessionOfflineMixin:
    """Offline review synchronization and error tracking."""

    def sync_offline_reviews(self) -> dict:
        """Submit queued offline promo reviews to Inflyte and Trackstack."""
        if self.storage is None:  # type: ignore[attr-defined]
            return {"synced": 0, "failed": 0, "remaining": 0}

        pending = self.storage.get_pending_offline_reviews()  # type: ignore[attr-defined]
        if not pending:
            return {"synced": 0, "failed": 0, "remaining": 0}

        synced = 0
        failed = 0
        for item in pending:
            rev_id = item["id"]
            src = item["source"]
            kind = item["decision"]
            rel_id = item["release_id"]
            rating = item["rating"]
            comment = item["comment"]
            fav_id = item["favourite_track_id"]
            promo_name = item["promo_name"]
            try:
                if src == "trackstack":
                    if self.trackstack_bridge is None:  # type: ignore[attr-defined]
                        raise SessionError("Trackstack bridge is not initialized")
                    promo_dict = self.storage.get_promo_cache(src, item["promo_key"]) or {}  # type: ignore[attr-defined]
                    if promo_dict:
                        promo_obj = InflytePromo.from_dict(promo_dict)
                    else:
                        promo_obj = InflytePromo(
                            release_id=rel_id,
                            name=promo_name,
                            sender_label="",
                            release_date="",
                            thumbnail_url="",
                            full_image_url="",
                            summary="",
                            source="trackstack",
                            submission_id=str(item["promo_key"]),
                        )
                    if kind.startswith("rate_"):
                        self.trackstack_bridge.accept_and_download_submission(promo_obj)  # type: ignore[attr-defined]
                    elif kind in ("dismiss", "decline"):
                        self.trackstack_bridge.decline_submission(promo_obj)  # type: ignore[attr-defined]
                else:
                    if self.bridge is None:  # type: ignore[attr-defined]
                        raise SessionError("Inflyte bridge is not initialized")
                    if kind.startswith("rate_"):
                        self.bridge.submit_feedback(  # type: ignore[attr-defined]
                            release_id=rel_id,
                            favourite_track_id=fav_id or 0,
                            rating=rating or 5,
                            comments=comment or "",
                        )
                    elif kind == "dismiss":
                        self.bridge.dismiss_promo(rel_id)  # type: ignore[attr-defined]

                self.storage.mark_offline_review_synced(rev_id)  # type: ignore[attr-defined]
                synced += 1
            except Exception as exc:
                failed += 1
                self.storage.mark_offline_review_failed(rev_id, str(exc))  # type: ignore[attr-defined]

        remaining = self.storage.get_offline_review_count()  # type: ignore[attr-defined]
        if synced:
            self.notify("info", f"✈️ Synced {synced} offline promo review(s)!")  # type: ignore[attr-defined]
        if failed:
            self.notify("warning", f"Could not sync {failed} offline review(s) (will retry later)")  # type: ignore[attr-defined]
        self._changed()  # type: ignore[attr-defined]
        return {"synced": synced, "failed": failed, "remaining": remaining}
