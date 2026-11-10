"""Trackstack WKWebView bridge and tRPC client."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ...bridge import BaseWebViewBridge
from ...config import safe_segment
from ...logger import get_logger
from ...ratelimit import RateLimitError, RateLimiter, call_with_backoff
from ...session import SessionError
from ...storage import EncryptedStorage, get_storage
from ...types import TrackstackAuthState
from ..models import InflytePromo, InflyteTrack, _INFLYTE_TRACK_IDS
from .client import (
    TRACKSTACK_APP_URL,
    TRACKSTACK_DEFAULT_API_URL,
    TRACKSTACK_STATIC_BASE_URL,
    TRACKSTACK_STREAM_BASE_URL,
    _INTERCEPTOR_JS,
    build_trpc_request_js,
)
from .downloader import (
    download_presigned_file,
    promo_download_dir,
    split_display_title,
    stable_int_id,
    unique_download_dest,
)

logger = get_logger("trackstack")


class TrackstackBridge(BaseWebViewBridge):
    """Communicates with ``https://my.trackstack.app`` and its tRPC API via an authenticated WKWebView."""

    def __init__(
        self,
        *,
        storage: Optional[EncryptedStorage] = None,
        limiter: Optional[RateLimiter] = None,
        download_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(window_title="Trackstack — Connect", url=TRACKSTACK_APP_URL, width=1080, height=760)
        self.storage = storage or get_storage()
        self.limiter = limiter or RateLimiter(rate_per_sec=4.0, burst=10)
        self._custom_download_dir = download_dir

        # Load cached non-secret workspace/inbox/stack IDs from encrypted SQLite if previously discovered
        cached_ctx = self.storage.get_kv("trackstack_context") or {}
        self.api_base_url: str = str(cached_ctx.get("api_base_url") or TRACKSTACK_DEFAULT_API_URL)
        self.workspace_id: Optional[str] = cached_ctx.get("workspace_id")
        self.inbox_id: Optional[str] = cached_ctx.get("inbox_id")
        self.user_id: Optional[str] = cached_ctx.get("user_id")
        self.downloaded_stack_id: Optional[str] = cached_ctx.get("downloaded_stack_id")
        self._is_connected: bool = bool(self.workspace_id and self.inbox_id)

    def _save_context(self) -> None:
        self.storage.put_kv(
            "trackstack_context",
            {
                "api_base_url": self.api_base_url,
                "workspace_id": self.workspace_id,
                "inbox_id": self.inbox_id,
                "user_id": self.user_id,
                "downloaded_stack_id": self.downloaded_stack_id,
            },
        )

    def _sync_state_from_js(self, st: dict) -> None:
        if not isinstance(st, dict):
            return
        changed = False
        if st.get("apiBaseUrl") and st["apiBaseUrl"] != self.api_base_url:
            self.api_base_url = str(st["apiBaseUrl"])
            changed = True
        if st.get("workspaceId") and st["workspaceId"] != self.workspace_id:
            self.workspace_id = str(st["workspaceId"])
            changed = True
        if st.get("inboxId") and st["inboxId"] != self.inbox_id:
            self.inbox_id = str(st["inboxId"])
            changed = True
        if st.get("userId") and st["userId"] != self.user_id:
            self.user_id = str(st["userId"])
            changed = True
        if st.get("ready"):
            self._is_connected = True
        if changed:
            self._save_context()

    def check_auth_status(self) -> dict:
        """Non-blocking check returning TrackstackAuthState dictionary without popping a window."""
        with self._lock:
            win = self._win
        if win is None:
            return TrackstackAuthState(
                connected=bool(self._is_connected or (self.workspace_id and self.inbox_id)),
                window_open=False,
                workspace_id=self.workspace_id,
                inbox_id=self.inbox_id,
                user_id=self.user_id,
                api_base_url=self.api_base_url,
            ).to_dict()
        try:
            st = self._eval_js(_INTERCEPTOR_JS, timeout=3.0, visible=False) or {}
            self._sync_state_from_js(st)
            ready = bool(st.get("ready"))
            if ready and self._login_window_shown:
                try:
                    self._login_window_shown = False
                    win.hide()
                except (AttributeError, RuntimeError) as hide_err:
                    logger.debug("Could not hide Trackstack login window: %s", hide_err)
            return TrackstackAuthState(
                connected=ready or self._is_connected,
                window_open=True,
                workspace_id=self.workspace_id,
                inbox_id=self.inbox_id,
                user_id=self.user_id,
                api_base_url=self.api_base_url,
            ).to_dict()
        except (SessionError, TimeoutError) as exc:
            logger.debug("Trackstack auth status check timed out or window inactive: %s", exc)
            return TrackstackAuthState(
                connected=self._is_connected,
                window_open=False,
                workspace_id=self.workspace_id,
                inbox_id=self.inbox_id,
                user_id=self.user_id,
                api_base_url=self.api_base_url,
            ).to_dict()

    def open_login_window(self) -> dict:
        """Show the authenticated Trackstack login window and poll for sign-in completion."""
        with self._lock:
            self._login_window_shown = True
        win = self._ensure_window(visible=True)
        try:
            win.show()
        except (AttributeError, RuntimeError):
            pass

        def on_auth_ready() -> None:
            with self._lock:
                self._login_window_shown = False
            try:
                win.hide()
            except (AttributeError, RuntimeError):
                pass
            self.ensure_context_ids()

        self._start_auth_watcher(
            interceptor_js=_INTERCEPTOR_JS,
            on_ready=on_auth_ready,
            timeout=300.0,
            poll_interval=1.0,
        )
        return self.check_auth_status()

    def ensure_authenticated(self, on_status: Optional[Callable[[str], None]] = None) -> None:
        """Verify the user is authenticated in Trackstack or pop the login window."""
        st = self.check_auth_status()
        if st.get("connected"):
            return

        if on_status:
            on_status("Waiting for Trackstack login window…")
        self.open_login_window()

        deadline = time.time() + 300.0
        while time.time() < deadline:
            time.sleep(1.0)
            st = self.check_auth_status()
            if st.get("connected"):
                if on_status:
                    on_status("Trackstack sign-in detected!")
                self.ensure_context_ids()
                return
        raise SessionError("Timed out waiting for Trackstack sign-in.")

    def _trpc_request(
        self,
        method: str,
        procedure: str,
        payload: Any = None,
        *,
        priority: bool = False,
    ) -> Any:
        """Execute an authenticated tRPC request inside the Trackstack WKWebView under rate-limit + backoff."""

        def do_call() -> Any:
            js = build_trpc_request_js(
                api_base_url=self.api_base_url,
                workspace_id=self.workspace_id,
                method=method,
                procedure=procedure,
                payload=payload,
            )
            res = self._eval_js(js, timeout=20.0) or {}
            status = int(res.get("status") or 0)
            if status in (429, 502, 503, 504):
                raise RateLimitError(
                    f"Trackstack HTTP {status} on {method} /{procedure}",
                    status_code=status,
                    retry_after=res.get("retryAfter"),
                )
            if status < 200 or status >= 300:
                raise SessionError(f"Trackstack HTTP {status} on {method} /{procedure}")
            body = res.get("data")
            if isinstance(body, dict) and "result" in body and isinstance(body["result"], dict):
                return body["result"].get("data")
            return body

        return call_with_backoff(do_call, limiter=self.limiter, priority=priority)

    def ensure_context_ids(self) -> None:
        """Discover ``workspaceId``, ``inboxId``, ``userId``, and ``downloaded_stack_id`` dynamically."""
        st = self._eval_js(_INTERCEPTOR_JS, timeout=6.0) or {}
        self._sync_state_from_js(st)

        if not self.user_id or not self.workspace_id:
            try:
                user_data = self._trpc_request("GET", "auth.getUser", None)
                if isinstance(user_data, dict):
                    if user_data.get("id"):
                        self.user_id = str(user_data["id"])
                    if not self.workspace_id and user_data.get("activeWorkspaceId"):
                        self.workspace_id = str(user_data["activeWorkspaceId"])
                    elif not self.workspace_id and isinstance(user_data.get("workspaces"), list) and user_data["workspaces"]:
                        ws0 = user_data["workspaces"][0]
                        if isinstance(ws0, dict) and ws0.get("id"):
                            self.workspace_id = str(ws0["id"])
            except (SessionError, RateLimitError) as exc:
                logger.debug("Non-fatal: auth.getUser failed during context discovery: %s", exc)

        if not self.inbox_id and self.workspace_id:
            try:
                ws_data = self._trpc_request("GET", "workspace.getWorkspace", self.workspace_id)
                if isinstance(ws_data, dict):
                    inboxes = ws_data.get("inboxes")
                    if isinstance(inboxes, list) and inboxes and isinstance(inboxes[0], dict) and inboxes[0].get("id"):
                        self.inbox_id = str(inboxes[0]["id"])
            except (SessionError, RateLimitError) as exc:
                logger.debug("Non-fatal: workspace.getWorkspace failed during context discovery: %s", exc)

        if not self.inbox_id:
            try:
                inbox_data = self._trpc_request("GET", "inbox.getInbox", None)
                if isinstance(inbox_data, dict) and inbox_data.get("id"):
                    self.inbox_id = str(inbox_data["id"])
            except (SessionError, RateLimitError) as exc:
                logger.debug("Non-fatal: inbox.getInbox failed during context discovery: %s", exc)

        if self.inbox_id and not self.downloaded_stack_id:
            try:
                stacks = self._trpc_request("GET", "inbox.getInboxStacks", {"inboxId": self.inbox_id})
                if isinstance(stacks, list):
                    for s in stacks:
                        if isinstance(s, dict) and str(s.get("name") or "").strip().lower() == "downloaded":
                            self.downloaded_stack_id = str(s["id"])
                            break
                    # If no stack named "Downloaded" exists yet, create one or use the first stack
                    if not self.downloaded_stack_id:
                        try:
                            created = self._trpc_request(
                                "POST",
                                "stacks.createStack",
                                {
                                    "data": {
                                        "inboxId": self.inbox_id,
                                        "name": "Downloaded",
                                        "commentBody": "",
                                        "isPublic": True,
                                        "type": "SHORTLIST",
                                    }
                                },
                            )
                            if isinstance(created, dict) and created.get("id"):
                                self.downloaded_stack_id = str(created["id"])
                        except (SessionError, RateLimitError) as exc:
                            logger.debug("Non-fatal: stacks.createStack failed: %s", exc)
                            if stacks and isinstance(stacks[0], dict) and stacks[0].get("id"):
                                self.downloaded_stack_id = str(stacks[0]["id"])
            except (SessionError, RateLimitError) as exc:
                logger.debug("Non-fatal: inbox.getInboxStacks failed: %s", exc)

        self._save_context()

    def fetch_pending_promos(
        self, on_status: Optional[Callable[[str], None]] = None
    ) -> list[InflytePromo]:
        """Fetch all pending Trackstack queue tracks and group them by ``submissionId`` into ``InflytePromo`` items."""
        self.ensure_authenticated(on_status=on_status)
        if on_status:
            on_status("Reading pending promos from Trackstack…")
        self.ensure_context_ids()
        if not self.inbox_id:
            raise SessionError("Could not determine Trackstack inboxId. Please open Trackstack Inbox once.")

        data = self._trpc_request(
            "GET",
            "submissions.listPendingQueueTracks",
            {
                "inboxId": self.inbox_id,
                "sortBy": "LAST_SENT",
                "viewType": "list",
                "filters": {},
                "limit": 300,
            },
            priority=True,
        )
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []

        return self.build_promos_from_items(items)

    def build_promos_from_items(self, items: list[dict]) -> list[InflytePromo]:
        """Group Trackstack ``listPendingQueueTracks`` items by ``submissionId`` into ``InflytePromo`` objects."""
        reviewed_keys = self.storage.get_reviewed_keys("trackstack")
        grouped: dict[str, list[dict]] = {}
        order: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("reviewState") or "PENDING").upper() != "PENDING":
                continue
            sub_id = str(item.get("submissionId") or item.get("id") or "").strip()
            if not sub_id or sub_id in reviewed_keys:
                continue
            if sub_id not in grouped:
                grouped[sub_id] = []
                order.append(sub_id)
            grouped[sub_id].append(item)

        promos: list[InflytePromo] = []
        for sub_id in order:
            sub_items = grouped[sub_id]
            if not sub_items:
                continue
            first = sub_items[0]
            release_int_id = stable_int_id(sub_id)
            sender = str(first.get("senderDisplayName") or "Trackstack Promo").strip()
            created_raw = str(first.get("createdAt") or "")[:10]
            first_title, first_artist = split_display_title(str(first.get("displayTitle") or ""), sender)
            promo_name = f"{first_artist} – {first_title}" if len(sub_items) == 1 else f"{first_artist or sender} ({len(sub_items)} Tracks)"

            tracks: list[InflyteTrack] = []
            total = len(sub_items)
            for idx, it in enumerate(sub_items):
                sub_track_id = str(it.get("id") or "").strip()
                track_uuid = str(it.get("trackId") or "").strip()
                if not sub_track_id:
                    continue
                t_title, t_artist = split_display_title(str(it.get("displayTitle") or f"Track {idx + 1}"), sender)
                stream_key = str(it.get("streamKey") or "").lstrip("/")
                stream_url = f"{TRACKSTACK_STREAM_BASE_URL}/{stream_key}" if stream_key else None
                bpm_val = it.get("bpm")
                genre_obj = it.get("genre") if isinstance(it.get("genre"), dict) else {}
                genre_label = str(genre_obj.get("label") or "").strip()

                tr = InflyteTrack(
                    id=str(next(_INFLYTE_TRACK_IDS)),
                    inflyte_track_id=stable_int_id(sub_track_id),
                    release_id=release_int_id,
                    name=t_title,
                    artist_name=t_artist,
                    duration=float(it.get("duration") or 0.0),
                    track_index=idx,
                    track_total=total,
                    peak_url="",
                    preview_url=stream_url,
                    source="trackstack",
                    submission_track_id=sub_track_id,
                    track_uuid=track_uuid,
                    bpm=float(bpm_val) if isinstance(bpm_val, (int, float)) and bpm_val > 0 else None,
                    genre=genre_label or None,
                )
                tracks.append(tr)

            if not tracks:
                continue

            image_key = str(first.get("imageKey") or "").lstrip("/")
            image_url = f"{TRACKSTACK_STATIC_BASE_URL}/{image_key}" if image_key else ""

            promo = InflytePromo(
                release_id=release_int_id,
                name=promo_name,
                sender_label=sender,
                release_date=created_raw,
                thumbnail_url=image_url,
                full_image_url=image_url,
                summary=sender,
                tracks=tracks,
                source="trackstack",
                submission_id=sub_id,
            )
            self.storage.put_promo_cache("trackstack", sub_id, {"submission_id": sub_id, "items": sub_items})
            promos.append(promo)

        return promos

    def mark_track_played(self, submission_track_id: str, play_start_position: int = 120) -> None:
        if not submission_track_id:
            return
        now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self._trpc_request(
            "POST",
            "submissions.createSubmissionTrackInteraction",
            {
                "submissionTrackId": str(submission_track_id),
                "type": "PLAY_START",
                "playStart": now_iso,
                "playStartPosition": int(play_start_position),
            },
            priority=False,
        )

    def decline_submission(self, promo: InflytePromo) -> None:
        """Decline (Thumbs Down ``← Not for me``) all tracks in a Trackstack submission."""
        for t in promo.tracks:
            if not t.submission_track_id:
                continue
            self._trpc_request(
                "POST",
                "submissions.defaultDeclineSubmissionTrack",
                {"id": t.submission_track_id},
                priority=True,
            )

    def accept_and_download_submission(
        self, promo: InflytePromo, dest_dir: Optional[Path] = None
    ) -> list[Path]:
        """Support (Thumbs Up) all tracks in a Trackstack submission and download them to ``~/Music/Inflyte Music``."""
        self.ensure_context_ids()
        if not self.downloaded_stack_id:
            raise SessionError("Could not find 'Downloaded' stack in Trackstack.")

        target_folder = dest_dir or promo_download_dir(
            str(self._custom_download_dir) if self._custom_download_dir else None
        )
        saved_paths: list[Path] = []

        for t in promo.tracks:
            if not t.submission_track_id:
                continue
            # 1. Move to "Downloaded" inbox (Thumbs Up)
            self._trpc_request(
                "POST",
                "submissions.supportSubmissionTrack",
                {
                    "id": t.submission_track_id,
                    "stackId": self.downloaded_stack_id,
                },
                priority=True,
            )

            # 2. Request presigned full-quality download URL (.wav / .aiff / .mp3)
            if t.track_uuid and self.workspace_id:
                dl_info = self._trpc_request(
                    "POST",
                    "tracks.createTrackDownloadUrl",
                    {
                        "trackId": t.track_uuid,
                        "submissionTrackId": t.submission_track_id,
                        "workspaceId": self.workspace_id,
                    },
                    priority=True,
                )
                if isinstance(dl_info, dict) and dl_info.get("presignedUrl"):
                    presigned_url = str(dl_info["presignedUrl"])
                    default_fname = f"{t.artist_name} - {t.name}.wav" if t.artist_name else f"{t.name}.wav"
                    filename = str(dl_info.get("filename") or default_fname)
                    dest_path = unique_download_dest(target_folder, filename)
                    downloaded = self._download_presigned_file(presigned_url, dest_path)
                    if downloaded is not None:
                        saved_paths.append(downloaded)

                    # 3. Record DOWNLOAD interaction in Trackstack
                    if self.user_id:
                        try:
                            self._trpc_request(
                                "POST",
                                "submissions.createSubmissionTrackInteraction",
                                {
                                    "type": "DOWNLOAD",
                                    "submissionTrackId": t.submission_track_id,
                                    "userId": self.user_id,
                                },
                                priority=False,
                            )
                        except (SessionError, RateLimitError) as exc:
                            logger.debug("Non-fatal: record interaction failed: %s", exc)

        return saved_paths

    def _download_presigned_file(self, url: str, dest: Path) -> Optional[Path]:
        """Atomically stream a full-quality Trackstack file to ``dest`` without overwriting existing files."""
        return download_presigned_file(url, dest)
