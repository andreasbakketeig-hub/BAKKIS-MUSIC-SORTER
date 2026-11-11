"""Promo reviewing and offline stash API mixin for AppBackend."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import inflyte
from ..server import ApiError

if TYPE_CHECKING:
    from ..config import PresetStore
    from ..duplicates import DuplicateIndex
    from ..library import ScanJob
    from ..session import Session
    from ..storage import EncryptedStorage
    from ..trackstack import TrackstackBridge


class PromoApiMixin:
    """API methods for Inflyte & Trackstack promo ingestion and offline stashing."""

    if TYPE_CHECKING:
        lock: threading.RLock
        scan: dict
        presets: PresetStore
        session: Optional[Session]
        dupes: Optional[DuplicateIndex]
        storage: EncryptedStorage
        inflyte_bridge: inflyte.InflyteBridge
        trackstack_bridge: TrackstackBridge
        comment_pool: inflyte.CommentPool
        _scan_job: Optional[ScanJob]
        _closed: bool

        def _changed(self) -> None: ...
        def _get_settings(self) -> dict: ...

    def api_open_comments_file(self) -> str:
        """Open promo_comments.txt in macOS default text editor."""
        return self.comment_pool.open_in_editor()

    def api_promo_comments_get(self) -> dict:
        """Return comment pools and configured rating numbers for Inflyte."""
        store = getattr(self, "storage", None)
        if store is not None:
            self.comment_pool.storage = store
            pools = store.get_promo_comments()
        else:
            pools = self.comment_pool.load_pools()
        settings = self._get_settings()
        return {
            "comments_5": pools.get(5, []),
            "comments_4": pools.get(4, []),
            "promo_rating_favorite": int(settings.get("promo_rating_favorite", 5)),
            "promo_rating_keep": int(settings.get("promo_rating_keep", 4)),
            "promo_download_dir": str(settings.get("promo_download_dir") or "~/Downloads"),
        }

    def api_promo_comments_save(
        self,
        comments_5: Optional[list[str]] = None,
        comments_4: Optional[list[str]] = None,
        promo_rating_favorite: int = 5,
        promo_rating_keep: int = 4,
        promo_download_dir: Optional[str] = None,
    ) -> dict:
        """Save comments pools to SQLite and update rating numbers in settings."""
        from ..storage import get_storage
        store = getattr(self, "storage", None) or get_storage()
        c5 = [str(c).strip() for c in (comments_5 or []) if str(c).strip()]
        c4 = [str(c).strip() for c in (comments_4 or []) if str(c).strip()]
        if store is not None:
            store.set_promo_comments({5: c5, 4: c4})

        # Also backup to user config promo_comments.txt if accessible
        try:
            from ..config import config_dir
            from ..promos.comments import COMMENTS_FILENAME
            cfg_path = config_dir() / COMMENTS_FILENAME
            lines = [
                "# ============================================================================",
                "# BAKKIS x Inflyte Auto-Feedback Comment Pool",
                "# ============================================================================",
                "",
                "[5/5]",
                *c5,
                "",
                "[4/5]",
                *c4,
                "",
            ]
            cfg_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

        # Update settings if api_set_settings exists
        fav = max(1, min(5, int(promo_rating_favorite)))
        keep = max(1, min(5, int(promo_rating_keep)))
        patch_kwargs: dict[str, Any] = {"promo_rating_favorite": fav, "promo_rating_keep": keep}
        clean_dl = str(promo_download_dir or "").strip()
        if clean_dl:
            patch_kwargs["promo_download_dir"] = clean_dl
        if hasattr(self, "api_set_settings"):
            self.api_set_settings(**patch_kwargs)

        return {
            "ok": True,
            "comments_5": c5,
            "comments_4": c4,
            "promo_rating_favorite": fav,
            "promo_rating_keep": keep,
            "promo_download_dir": clean_dl or "~/Downloads",
        }

    def api_auth_status(self) -> dict:
        """Return live connection status for both Inflyte and Trackstack without opening windows."""
        inf_st = self.inflyte_bridge.check_auth_status()
        ts_st = self.trackstack_bridge.check_auth_status()
        return {
            "inflyte": inf_st,
            "trackstack": ts_st,
        }

    def api_login_inflyte(self) -> dict:
        """Open or focus the Inflyte login window and return current auth status."""
        self.inflyte_bridge.open_login_window()
        return self.api_auth_status()

    def api_login_trackstack(self) -> dict:
        """Open or focus the Trackstack login window and return current auth status."""
        self.trackstack_bridge.open_login_window()
        return self.api_auth_status()

    def api_load_promos(self, source: str = "all") -> dict:
        """Load unreviewed promos from Inflyte, Trackstack, or both ('all') into a unified Promo Session."""
        return self.api_load_inflyte(source=source)

    def api_load_inflyte(self, source: str = "all") -> dict:
        """Load unreviewed promos from Inflyte + Trackstack into a unified multi-track PromoSession."""
        src_mode = str(source or "all").strip().lower()
        with self.lock:
            if self._scan_job is not None:
                self._scan_job.cancel()
                self._scan_job = None
            # Switch active swipe preset to 'Promos' automatically if present
            for p in self.presets.of_mode("swipe"):
                if p["name"].strip().lower() == "promos":
                    self.presets.set_active(p["id"])
                    break
            root_title = (
                "Trackstack Promos"
                if src_mode == "trackstack"
                else ("Inflyte Promo Box" if src_mode == "inflyte" else "All Promos (Inflyte + Trackstack)")
            )
            self.scan = {
                "status": "scanning",
                "root": root_title,
                "count": 0,
                "message": f"Connecting to {root_title}…",
                "error": None,
            }
            old = self.session

        def on_status(msg: str) -> None:
            with self.lock:
                self.scan["message"] = msg
            self._changed()

        def ensure_session_started(
            initial_list: list[inflyte.InflytePromo], current_active: Optional[inflyte.InflyteSession]
        ) -> inflyte.InflyteSession:
            if current_active is not None:
                for item in initial_list:
                    current_active.append_promo(item)
                with self.lock:
                    self.scan["count"] = current_active.total
                self._changed()
                return current_active
            with self.lock:
                if self.dupes is not None:
                    self.dupes.cancel()
                    self.dupes = None
                new_sess = inflyte.InflyteSession(
                    initial_list,
                    bridge=self.inflyte_bridge,
                    trackstack_bridge=self.trackstack_bridge,
                    storage=self.storage,
                    presets=self.presets,
                    settings=self._get_settings,
                    comment_pool=self.comment_pool,
                    on_change=self._changed,
                    lock=self.lock,
                )
                self.session = new_sess  # type: ignore[assignment]
                self.scan.update(status="ready", count=new_sess.total, message=None)
            self._changed()
            return new_sess

        def worker() -> None:
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            try:
                active_session: Optional[inflyte.InflyteSession] = None
                inf_status = self.inflyte_bridge.check_auth_status()
                ts_status = self.trackstack_bridge.check_auth_status()

                if src_mode == "inflyte":
                    want_inflyte, want_trackstack = True, False
                elif src_mode == "trackstack":
                    want_inflyte, want_trackstack = False, True
                else:
                    both_disconnected = not inf_status.get("connected") and not ts_status.get("connected")
                    want_inflyte = bool(inf_status.get("connected") or inf_status.get("window_open") or both_disconnected)
                    want_trackstack = bool(ts_status.get("connected") or ts_status.get("window_open"))

                # 1. Load Trackstack promos first if requested (single fast API call!)
                if want_trackstack:
                    try:
                        ts_promos = self.trackstack_bridge.fetch_pending_promos(on_status=on_status)
                        if ts_promos and not self._closed:
                            active_session = ensure_session_started(ts_promos, active_session)
                    except Exception as ts_exc:
                        if src_mode == "trackstack":
                            raise ts_exc

                # 2. Load Inflyte promos if requested
                if want_inflyte and not self._closed:
                    try:
                        hydrated_promos, release_ids = self.inflyte_bridge.fetch_initial_promos(on_status=on_status)
                        seen_ids: set[int] = {p.release_id for p in hydrated_promos} | set(release_ids)
                        queue_ids: list[int] = list(release_ids)

                        if hydrated_promos and not self._closed:
                            active_session = ensure_session_started(hydrated_promos, active_session)

                        while queue_ids:
                            if self._closed:
                                return
                            rid = queue_ids.pop(0)
                            try:
                                p, extra_ids = self.inflyte_bridge.fetch_release(rid)
                            except Exception:
                                continue
                            for ex_id in extra_ids:
                                if ex_id not in seen_ids:
                                    seen_ids.add(ex_id)
                                    queue_ids.append(ex_id)
                            if p is None or not p.tracks:
                                continue
                            active_session = ensure_session_started([p], active_session)
                    except Exception as inf_exc:
                        if src_mode == "inflyte" or active_session is None:
                            stashed_raw = self.storage.get_all_stashed_promos() if self.storage else []
                            if stashed_raw:
                                stashed_promos = [inflyte.InflytePromo.from_dict(d) for d in stashed_raw]
                                active_session = ensure_session_started(stashed_promos, active_session)
                                if active_session is not None:
                                    active_session.offline_mode = True
                                    active_session.notify("info", f"✈️ Loaded {len(stashed_promos)} stashed promo(s) in offline mode")
                                    return
                            raise inf_exc

                if active_session is None and not self._closed:
                    ensure_session_started([], None)
            except Exception as exc:
                with self.lock:
                    self.scan.update(status="error", error=str(exc), message=None)
                self._changed()

        threading.Thread(target=worker, daemon=True, name="promo-loader").start()
        self._changed()
        return dict(self.scan)

    def api_stash_promos(self, count: int = 50) -> dict:
        """Pre-download audio and artwork for upcoming promos and store encrypted metadata for offline flight use."""
        if not getattr(self.session, "is_inflyte", False):
            raise ApiError("Stash mode is only available for Promo reviewing")
        return self.session.stash_promos(target_count=int(count))  # type: ignore[attr-defined]

    def api_stash_status(self) -> dict:
        """Get current download progress of promo stash and pending offline reviews."""
        if getattr(self.session, "is_inflyte", False):
            return self.session.get_stash_state()  # type: ignore[attr-defined]
        queued = self.storage.get_offline_review_count() if self.storage else 0
        return {"status": "idle", "queued_offline": queued}

    def api_sync_offline_reviews(self) -> dict:
        """Submit all pending offline reviews recorded during flight to Inflyte and Trackstack."""
        if getattr(self.session, "is_inflyte", False):
            return self.session.sync_offline_reviews()  # type: ignore[attr-defined]
        raise ApiError("Sync reviews is only available for Promo reviewing")

    def api_toggle_offline_mode(self, enabled: Optional[bool] = None) -> dict:
        """Toggle or explicitly set offline mode for the active Promo reviewer."""
        if getattr(self.session, "is_inflyte", False):
            s = self.session
            if enabled is None:
                s.offline_mode = not s.offline_mode  # type: ignore[attr-defined]
            else:
                s.offline_mode = bool(enabled)  # type: ignore[attr-defined]
            self._changed()
            return {"offline_mode": s.offline_mode}  # type: ignore[attr-defined]
        return {"offline_mode": False}

    def api_promo_history(
        self,
        limit: int = 100,
        offset: int = 0,
        source: str = "",
        decision: str = "",
        search: str = "",
    ) -> dict:
        """Return decrypted past promo reviews and summary counts."""
        if self.storage is None:
            return {"reviews": [], "total": 0, "ok": True}
        reviews, total = self.storage.get_promo_review_history(
            limit=int(limit),
            offset=int(offset),
            source=source or None,
            decision=decision or None,
            search=search or None,
        )
        return {"reviews": reviews, "total": total, "ok": True}
