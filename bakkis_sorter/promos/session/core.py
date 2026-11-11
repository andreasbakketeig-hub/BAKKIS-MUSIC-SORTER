"""Core state, queue traversal, and metadata management for promo sessions."""

from __future__ import annotations

import itertools
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ...duplicates import track_signature
from ...logger import get_logger
from ...ratelimit import RateLimitError
from ...session import HISTORY_PREVIEW, MAX_NOTICES, SessionError, Worker
from ...storage import EncryptedStorage, get_storage
from ..comments import CommentPool
from ..inflyte import INFLYTE_PROMOS_URL, cache_dir
from ..models import InflyteAction, InflytePromo, InflyteTrack

logger = get_logger("inflyte")


class SessionCoreMixin:
    """Core state machine, initialization, pointers, track metadata, and queue slicing."""

    is_inflyte = True

    def __init__(
        self,
        promos: list[InflytePromo],
        *,
        bridge: Any,
        trackstack_bridge: Optional[Any] = None,
        storage: Optional[EncryptedStorage] = None,
        presets: Any,
        settings: Callable[[], dict],
        comment_pool: Optional[CommentPool] = None,
        on_change: Optional[Callable[[], None]] = None,
        lock: Optional[threading.RLock] = None,
    ) -> None:
        self.music_root = Path(INFLYTE_PROMOS_URL)
        self.bridge = bridge
        self.trackstack_bridge = trackstack_bridge
        self.storage = storage or getattr(bridge, "storage", None) or get_storage()
        self.presets = presets
        self._settings = settings
        self.comments = comment_pool or CommentPool()
        self._on_change = on_change
        self.lock = lock or threading.RLock()

        self.promos: list[InflytePromo] = []
        self._seen_promo_keys: set[tuple[str, str]] = set()
        self.current_track_idx: int = 0
        self.total: int = 0
        self.held: list = []
        self.filter: Optional[dict] = None

        self.tracks: dict[str, InflyteTrack] = {}
        self.track_to_promo: dict[str, InflytePromo] = {}

        self._action_ids = itertools.count(1)
        self.history: list[InflyteAction] = []
        self.counts: dict[str, int] = {}
        self.count_info: dict[str, dict] = {}
        self.notices: deque = deque(maxlen=MAX_NOTICES)
        self._notice_seq = itertools.count(1)
        self.started_at = time.time()
        self.session_id = f"promos-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._audio_locks: dict[str, threading.Lock] = {}
        self._played_sent: set[int] = set()
        self._prefetched_ids: set[str] = set()
        self._prefetch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="promo-prefetch")
        self._worker = Worker(on_error=lambda e: self.notify("error", f"Promo API error: {e}"))

        self.offline_mode: bool = False
        self.stash_state: dict[str, Any] = {
            "status": "idle",
            "target_count": 0,
            "completed_promos": 0,
            "completed_tracks": 0,
            "total_bytes": 0,
            "message": "",
            "error": None,
        }
        self._stash_thread: Optional[threading.Thread] = None

        for p in promos:
            self.append_promo(p, notify=False)
        self._prefetch_nearby()

    def append_promo(self, p: InflytePromo, notify: bool = True) -> bool:
        """Append a newly fetched promo dynamically to the live session queue."""
        if not p.tracks:
            return False
        key = (p.source, p.promo_key)
        with self.lock:
            if key in self._seen_promo_keys:
                return False
            self._seen_promo_keys.add(key)
            self.promos.append(p)
            self.total += 1
            for t in p.tracks:
                self.tracks[t.id] = t
                self.track_to_promo[t.id] = p
            should_prefetch = len(self.promos) <= 9
        if notify:
            if should_prefetch:
                self._prefetch_nearby()
            self._changed()
        return True

    def operation(self) -> str:
        return "inflyte"

    @property
    def current(self) -> Optional[InflyteTrack]:
        with self.lock:
            if not self.promos:
                return None
            promo = self.promos[0]
            idx = max(0, min(self.current_track_idx, len(promo.tracks) - 1))
            return promo.tracks[idx]

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    def notify(self, level: str, message: str, **extra: object) -> None:
        with self.lock:
            self.notices.append({"seq": next(self._notice_seq), "level": level, "message": message, **extra})
        self._changed()

    def notices_since(self, seq: int) -> list[dict]:
        with self.lock:
            return [n for n in self.notices if n["seq"] > seq]

    def flush(self) -> None:
        self._worker.flush()

    def close(self, timeout: Optional[float] = 15.0) -> bool:
        try:
            self._prefetch_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        done = self._worker.flush(timeout)
        self._worker.stop()
        return done

    def active_dests(self) -> frozenset:
        return frozenset()

    def folder_count(self, preset: dict, slot: int) -> int:
        with self.lock:
            return self.counts.get(f"{preset['id']}:{slot}", 0)

    def track_path(self, track_id: str) -> Optional[Path]:
        """Download/cache the preview stream for ``track_id``, mark it played, and return its local Path."""
        path = self._ensure_audio_cached(track_id, silent=False)  # type: ignore[attr-defined]
        if path is not None:
            with self.lock:
                t = self.tracks.get(str(track_id))
            if t is not None:
                self._mark_played_async(t)
        return path

    def _mark_played_async(self, t: InflyteTrack) -> None:
        with self.lock:
            if t.inflyte_track_id in self._played_sent:
                return
            self._played_sent.add(t.inflyte_track_id)
        threading.Thread(
            target=lambda: self._safe_mark_played(t),
            daemon=True,
        ).start()

    def _safe_mark_played(self, t: InflyteTrack) -> None:
        try:
            if t.source == "trackstack" and self.trackstack_bridge is not None and t.submission_track_id:
                self.trackstack_bridge.mark_track_played(t.submission_track_id)
            elif self.bridge is not None:
                self.bridge.mark_track_played(t.release_id, t.inflyte_track_id)
        except (SessionError, RateLimitError) as exc:
            logger.debug("Non-fatal: mark_track_played failed: %s", exc)

    def cover(self, track_id: str) -> Optional[bytes]:
        with self.lock:
            p = self.track_to_promo.get(str(track_id))
        if p is None:
            return None
        if p.cached_cover_path and p.cached_cover_path.is_file():
            try:
                return p.cached_cover_path.read_bytes()
            except OSError:
                pass
        url = p.full_image_url or p.thumbnail_url
        if not url:
            return None
        dest = cache_dir() / f"cover_{p.source}_{p.release_id}.jpg"
        downloaded = self._download_file(url, dest)  # type: ignore[attr-defined]
        if downloaded:
            p.cached_cover_path = downloaded
            try:
                return downloaded.read_bytes()
            except OSError:
                return None
        return None

    def track_meta(self, track_id: str) -> dict:
        with self.lock:
            t = self.tracks.get(str(track_id))
            p = self.track_to_promo.get(str(track_id))
        if t is None or p is None:
            raise SessionError("Unknown promo track")
        size = t.cached_audio_path.stat().st_size if (t.cached_audio_path and t.cached_audio_path.is_file()) else 0
        dup_review = None
        if self.storage is not None:
            sig = track_signature(t.artist_name or p.name, t.name)
            if sig:
                dup_review = self.storage.get_track_signature_match(sig)
        bpm = t.bpm
        key_str = f"Track {t.track_index + 1}/{t.track_total}"
        camelot = None
        drop_seconds = None
        jump_delta = round(32.0 * (60.0 / float(bpm)), 2) if (bpm and float(bpm) > 0) else 15.24

        if t.cached_audio_path and t.cached_audio_path.is_file():
            try:
                from ...metadata import read_meta

                m = read_meta(t.cached_audio_path)
                if m.bpm:
                    bpm = float(m.bpm)
                if m.camelot:
                    camelot = m.camelot
                if m.drop_seconds is not None:
                    drop_seconds = m.drop_seconds
                if m.jump_delta:
                    jump_delta = m.jump_delta
            except Exception:
                pass

        return {
            "track_id": t.id,
            "title": t.name,
            "artist": t.artist_name or p.name,
            "album": p.name,
            "display": f"{t.name} — {t.artist_name}" if t.artist_name else t.name,
            "duration": t.duration,
            "bpm": bpm,
            "camelot": camelot,
            "drop_seconds": drop_seconds,
            "jump_delta": jump_delta,
            "key": key_str,
            "has_cover": bool(p.full_image_url or p.thumbnail_url),
            "size": size or 8_000_000,
            "duplicate": None,
            "duplicate_review": dup_review,
            "pcm": False,
            "source": p.source,
            "inflyte": {
                "source": p.source,
                "release_id": p.release_id,
                "submission_id": p.submission_id,
                "inflyte_track_id": t.inflyte_track_id,
                "track_index": t.track_index + 1,
                "track_total": t.track_total,
                "sender_label": p.sender_label,
                "release_date": p.release_date,
                "promo_name": p.name,
                "genre": t.genre,
            },
        }

    def set_order(self, order: str) -> None:
        with self.lock:
            if len(self.promos) <= 1:
                return
            head = self.promos[0]
            rest = self.promos[1:]
            if order == "random":
                random.shuffle(rest)
            else:
                rest.sort(key=lambda p: p.release_id, reverse=True)
            self.promos = [head] + rest

    def play_next(self, track_id: str) -> None:
        with self.lock:
            for i, p in enumerate(self.promos):
                if any(t.id == str(track_id) for t in p.tracks):
                    if i > 1:
                        self.promos.insert(1, self.promos.pop(i))
                    return
            raise SessionError("Promo not in queue")

    def queue_slice(self, offset: int = 0, limit: int = 100, q: str = "") -> dict:
        offset, limit = max(0, int(offset)), max(0, min(int(limit), 500))
        words = [w for w in str(q or "").lower().split() if w]
        with self.lock:
            upcoming_tracks: list[InflyteTrack] = []
            if self.promos:
                cur_p = self.promos[0]
                for t in cur_p.tracks[self.current_track_idx + 1 :]:
                    upcoming_tracks.append(t)
                for p in self.promos[1:]:
                    if p.tracks:
                        upcoming_tracks.append(p.tracks[0])

            indexed = list(enumerate(upcoming_tracks))
            if words:
                indexed = [
                    (i, t)
                    for i, t in indexed
                    if all(w in f"{t.artist_name} {t.name} {self.track_to_promo[t.id].name}".lower() for w in words)
                ]
            page = indexed[offset : offset + limit]
            items = [dict(self._track_public(t), index=i) for i, t in page]
            return {"total": len(indexed), "offset": offset, "items": items, "query": " ".join(words)}

    def snapshot(self, upcoming: int = 8) -> dict:
        with self.lock:
            done = self.total - len(self.promos)
            cur = self.current
            up_list: list[dict] = []
            next_promo_track_id: Optional[str] = None
            next_in_promo_track_id: Optional[str] = None
            if self.promos:
                cur_p = self.promos[0]
                if len(self.promos) > 1 and self.promos[1].tracks:
                    next_promo_track_id = self.promos[1].tracks[0].id
                if self.current_track_idx + 1 < len(cur_p.tracks):
                    next_in_promo_track_id = cur_p.tracks[self.current_track_idx + 1].id

                for p in self.promos[1:]:
                    if p.tracks and len(up_list) < upcoming:
                        up_list.append(self._track_public(p.tracks[0]))
                for t in cur_p.tracks[self.current_track_idx + 1 :]:
                    if len(up_list) < upcoming:
                        up_list.append(self._track_public(t))

            return {
                "root": "Inflyte Promo Box",
                "is_inflyte": True,
                "total": self.total,
                "position": min(done + 1, self.total),
                "remaining": len(self.promos),
                "held": 0,
                "filter": None,
                "current": self._track_public(cur) if cur else None,
                "upcoming": up_list,
                "next_promo_track_id": next_promo_track_id,
                "next_in_promo_track_id": next_in_promo_track_id,
                "history": [a.public() for a in self.history[-HISTORY_PREVIEW:]][::-1],
                "counts": dict(self.counts),
                "count_info": dict(self.count_info),
                "sorted": sum(v for k, v in self.counts.items() if k != "skip") + self.counts.get("skip", 0),
                "pending": sum(1 for a in self.history if a.status == "pending"),
                "elapsed": time.time() - self.started_at,
                "last_notice": self.notices[-1]["seq"] if self.notices else 0,
                "operation": "inflyte",
                "in_place": False,
                "offline_mode": self.offline_mode,
                "stash_state": dict(self.stash_state),
                "offline_queued_count": self.storage.get_offline_review_count() if self.storage else 0,
            }

    def _track_public(self, t: InflyteTrack) -> dict:
        p = self.track_to_promo[t.id]
        track_badge = f"Track {t.track_index + 1} of {t.track_total}"
        label_part = p.sender_label or p.name
        ext = "aac" if p.source == "trackstack" else "mp3"
        dup_review = None
        if self.storage is not None:
            sig = track_signature(t.artist_name or p.name, t.name)
            if sig:
                dup_review = self.storage.get_track_signature_match(sig)
        return {
            "id": t.id,
            "name": f"{t.artist_name} — {t.name}.{ext}" if t.artist_name else f"{t.name}.{ext}",
            "folder": f"{label_part} · {track_badge}",
            "rel": f"{label_part}/{track_badge}/{t.name}.{ext}",
            "track_index": t.track_index + 1,
            "track_total": t.track_total,
            "promo_name": p.name,
            "release_id": p.release_id,
            "source": p.source,
            "duplicate_review": dup_review,
        }
