"""Promo session mixin for sorting, skipping, undoing, and submitting promo reviews."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import Optional

from ...duplicates import track_signature
from ...session import SKIP_SLOT, SessionError
from ..models import InflyteAction, InflytePromo


class SessionActionsMixin:
    """Action execution, decision routing, undo stack, and feedback dispatching."""

    def skip(self, track_id: str, mode: str = "swipe") -> InflyteAction:
        """``↓`` button:
        - If there are more tracks in the current promo, advance to the next track in the same promo.
        - If already on the last track of the promo, skip feedback on this promo for now and move to
          Track 1 of the next promo.
        """
        with self.lock:  # type: ignore[attr-defined]
            cur = self.current  # type: ignore[attr-defined]
            if cur is None:
                raise SessionError("Queue is empty")
            if cur.id != str(track_id):
                raise SessionError("Track is no longer current")
            promo = self.promos[0]  # type: ignore[attr-defined]
            preset = (
                self.presets.active("swipe")  # type: ignore[attr-defined]
                if self.presets is not None  # type: ignore[attr-defined]
                else {
                    "labels": ["Keep", "Archive", "Favorite"],
                    "colors": ["#4cd137", "#e84118", "#00a8ff"],
                    "id": "promos",
                    "name": "Promos",
                }
            )
            prev_idx = self.current_track_idx  # type: ignore[attr-defined]

            if self.current_track_idx + 1 < len(promo.tracks):  # type: ignore[attr-defined]
                self.current_track_idx += 1  # type: ignore[attr-defined]
                next_t = promo.tracks[self.current_track_idx]  # type: ignore[attr-defined]
                act = InflyteAction(
                    id=next(self._action_ids),  # type: ignore[attr-defined]
                    promo=promo,
                    track=cur,
                    prev_track_idx=prev_idx,
                    slot=SKIP_SLOT,
                    kind="next_track",
                    label=f"Track {next_t.track_index + 1}/{next_t.track_total}",
                    preset_id=preset["id"],
                    preset_name=preset["name"],
                    status="done",
                )
                self.history.append(act)  # type: ignore[attr-defined]
                self.notify(  # type: ignore[attr-defined]
                    "info",
                    f"Track {next_t.track_index + 1} of {next_t.track_total}: “{next_t.name}”",
                )
            else:
                self.promos.pop(0)  # type: ignore[attr-defined]
                self.current_track_idx = 0  # type: ignore[attr-defined]
                act = InflyteAction(
                    id=next(self._action_ids),  # type: ignore[attr-defined]
                    promo=promo,
                    track=cur,
                    prev_track_idx=prev_idx,
                    slot=SKIP_SLOT,
                    kind="skip_promo",
                    label="Skipped promo",
                    preset_id=preset["id"],
                    preset_name=preset["name"],
                    status="done",
                )
                self.history.append(act)  # type: ignore[attr-defined]
                self.counts["skip"] = self.counts.get("skip", 0) + 1  # type: ignore[attr-defined]
                self.notify("info", f"Skipped promo “{promo.name}” for now")  # type: ignore[attr-defined]

        self._prefetch_nearby()  # type: ignore[attr-defined]
        self._changed()  # type: ignore[attr-defined]
        return act

    def sort(self, track_id: str, mode: str, slot: int) -> InflyteAction:
        """Handle swipe decisions on the current Inflyte or Trackstack promo."""
        with self.lock:  # type: ignore[attr-defined]
            cur = self.current  # type: ignore[attr-defined]
            if cur is None:
                raise SessionError("Queue is empty")
            if cur.id != str(track_id):
                raise SessionError("Track is no longer current")
            slot_i = int(slot)
            if slot_i not in (0, 1, 2):
                raise SessionError(f"Unsupported promo slot: {slot}")

            promo = self.promos.pop(0)  # type: ignore[attr-defined]
            prev_idx = self.current_track_idx  # type: ignore[attr-defined]
            self.current_track_idx = 0  # type: ignore[attr-defined]
            preset = (
                self.presets.active("swipe")  # type: ignore[attr-defined]
                if self.presets is not None  # type: ignore[attr-defined]
                else {
                    "labels": ["Keep", "Archive", "Favorite"],
                    "colors": ["#4cd137", "#e84118", "#00a8ff"],
                    "id": "promos",
                    "name": "Promos",
                }
            )
            label = preset["labels"][slot_i]

            settings_dict = self._settings() if callable(self._settings) else {}  # type: ignore[attr-defined]
            rating_fav = max(1, min(5, int(settings_dict.get("promo_rating_favorite", 5))))
            rating_keep = max(1, min(5, int(settings_dict.get("promo_rating_keep", 4))))

            if promo.source == "trackstack":
                rating = None
                comment = None
                kind = f"rate_{rating_fav}" if slot_i == 2 else (f"rate_{rating_keep}" if slot_i == 0 else "dismiss")
            elif slot_i == 2:
                rating = rating_fav
                comment = self.comments.pick(5)  # type: ignore[attr-defined]
                kind = f"rate_{rating_fav}"
            elif slot_i == 0:
                rating = rating_keep
                comment = self.comments.pick(4)  # type: ignore[attr-defined]
                kind = f"rate_{rating_keep}"
            else:
                rating = None
                comment = None
                kind = "dismiss"

            act = InflyteAction(
                id=next(self._action_ids),  # type: ignore[attr-defined]
                promo=promo,
                track=cur,
                prev_track_idx=prev_idx,
                slot=slot_i,
                kind=kind,
                label=label,
                preset_id=preset["id"],
                preset_name=preset["name"],
                rating=rating,
                comment=comment,
            )
            self.history.append(act)  # type: ignore[attr-defined]
            self.counts[act.count_key] = self.counts.get(act.count_key, 0) + 1  # type: ignore[attr-defined]
            self.count_info.setdefault(  # type: ignore[attr-defined]
                act.count_key,
                {
                    "label": label,
                    "preset": preset["name"],
                    "color": preset["colors"][slot_i],
                    "slot": slot_i,
                    "mode": "swipe",
                },
            )
            self._worker.submit(lambda: self._run_action(act))  # type: ignore[attr-defined]

        self._prefetch_nearby()  # type: ignore[attr-defined]
        self._changed()  # type: ignore[attr-defined]
        return act

    def _promo_media_paths(self, promo: InflytePromo) -> list[Path]:
        paths: list[Path] = []
        if promo.cached_cover_path:
            paths.append(promo.cached_cover_path)
        for tr in promo.tracks:
            if tr.cached_audio_path:
                paths.append(tr.cached_audio_path)
        return paths

    def _run_action(self, a: InflyteAction) -> None:
        with self.lock:  # type: ignore[attr-defined]
            if a.undo_requested:
                a.status = "cancelled"
                return
        if getattr(self, "offline_mode", False) and self.storage is not None and (a.kind.startswith("rate_") or a.kind == "dismiss"):  # type: ignore[attr-defined]
            with self.lock:  # type: ignore[attr-defined]
                a.status = "queued_offline"
            self.storage.queue_offline_review(  # type: ignore[attr-defined]
                source=a.promo.source,
                promo_key=a.promo.promo_key,
                release_id=a.promo.release_id,
                decision=a.kind,
                rating=a.rating,
                comment=a.comment,
                favourite_track_id=a.track.inflyte_track_id if a.track else None,
                promo_name=a.promo.name,
                payload={"promo_name": a.promo.name, "rating": a.rating, "comment": a.comment},
                media_paths=self._promo_media_paths(a.promo),
            )
            for tr in a.promo.tracks:
                sig = track_signature(tr.artist_name or a.promo.name, tr.name)
                if sig:
                    self.storage.record_track_signature(  # type: ignore[attr-defined]
                        signature=sig,
                        artist=tr.artist_name or a.promo.name,
                        title=tr.name,
                        decision=a.kind,
                        rating=a.rating,
                        source=a.promo.source,
                        promo_name=a.promo.name,
                    )
            self.notify("info", f"✈️ Offline: Queued “{a.promo.name}” for sync when reconnected")  # type: ignore[attr-defined]
            self._changed()  # type: ignore[attr-defined]
            return

        try:
            if a.promo.source == "trackstack":
                if self.trackstack_bridge is None:  # type: ignore[attr-defined]
                    raise SessionError("Trackstack bridge is not initialized")
                if a.kind.startswith("rate_"):
                    saved_files = self.trackstack_bridge.accept_and_download_submission(a.promo)  # type: ignore[attr-defined]
                    with self.lock:  # type: ignore[attr-defined]
                        a.status = "done"
                    if self.storage is not None:  # type: ignore[attr-defined]
                        self.storage.record_review(  # type: ignore[attr-defined]
                            "trackstack",
                            a.promo.promo_key,
                            a.kind,
                            {
                                "promo_name": a.promo.name,
                                "artist": getattr(a.track, "artist_name", "") if a.track else "",
                                "title": getattr(a.track, "name", "") if a.track else "",
                                "downloaded": [str(p) for p in saved_files],
                            },
                            media_paths=self._promo_media_paths(a.promo),
                        )
                    dl_note = f"Downloaded {len(saved_files)} file(s) to ~/Music/Inflyte Music" if saved_files else "Moved to Downloaded"
                    self.notify("info", f"Trackstack 👍 “{a.promo.name}” · {dl_note}")  # type: ignore[attr-defined]
                elif a.kind == "dismiss":
                    self.trackstack_bridge.decline_submission(a.promo)  # type: ignore[attr-defined]
                    with self.lock:  # type: ignore[attr-defined]
                        a.status = "done"
                    if self.storage is not None:  # type: ignore[attr-defined]
                        self.storage.record_review(  # type: ignore[attr-defined]
                            "trackstack",
                            a.promo.promo_key,
                            "decline",
                            {
                                "promo_name": a.promo.name,
                                "artist": getattr(a.track, "artist_name", "") if a.track else "",
                                "title": getattr(a.track, "name", "") if a.track else "",
                            },
                            media_paths=self._promo_media_paths(a.promo),
                        )
                    self.notify("info", f"Declined 👎 “{a.promo.name}” on Trackstack")  # type: ignore[attr-defined]
            else:
                if a.kind.startswith("rate_"):
                    assert a.rating is not None and a.comment is not None
                    self.bridge.submit_feedback(  # type: ignore[attr-defined]
                        release_id=a.promo.release_id,
                        favourite_track_id=a.track.inflyte_track_id,
                        rating=a.rating,
                        comments=a.comment,
                    )
                    with self.lock:  # type: ignore[attr-defined]
                        a.status = "done"
                    if self.storage is not None:  # type: ignore[attr-defined]
                        self.storage.record_review(  # type: ignore[attr-defined]
                            "inflyte",
                            a.promo.promo_key,
                            a.kind,
                            {
                                "promo_name": a.promo.name,
                                "artist": getattr(a.track, "artist_name", "") if a.track else "",
                                "title": getattr(a.track, "name", "") if a.track else "",
                                "rating": a.rating,
                                "comment": a.comment,
                            },
                            media_paths=self._promo_media_paths(a.promo),
                        )
                    self.notify(  # type: ignore[attr-defined]
                        "info",
                        f"Submitted {a.rating}/5 (“{a.comment}”) · Syncing all {len(a.promo.tracks)} track(s) to Dropbox",
                    )
                elif a.kind == "dismiss":
                    self.bridge.dismiss_promo(a.promo.release_id)  # type: ignore[attr-defined]
                    with self.lock:  # type: ignore[attr-defined]
                        a.status = "done"
                    if self.storage is not None:  # type: ignore[attr-defined]
                        self.storage.record_review(  # type: ignore[attr-defined]
                            "inflyte",
                            a.promo.promo_key,
                            "dismiss",
                            {
                                "promo_name": a.promo.name,
                                "artist": getattr(a.track, "artist_name", "") if a.track else "",
                                "title": getattr(a.track, "name", "") if a.track else "",
                            },
                            media_paths=self._promo_media_paths(a.promo),
                        )
                    self.notify("info", f"Dismissed “{a.promo.name}” from Inflyte inbox")  # type: ignore[attr-defined]
            if self.storage is not None:  # type: ignore[attr-defined]
                for tr in a.promo.tracks:
                    sig = track_signature(tr.artist_name or a.promo.name, tr.name)
                    if sig:
                        self.storage.record_track_signature(  # type: ignore[attr-defined]
                            signature=sig,
                            artist=tr.artist_name or a.promo.name,
                            title=tr.name,
                            decision=a.kind,
                            rating=a.rating,
                            source=a.promo.source,
                            promo_name=a.promo.name,
                        )
        except Exception as exc:
            is_net_error = (
                isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))
                or "offline" in str(exc).lower()
                or "network" in str(exc).lower()
                or "connection" in str(exc).lower()
                or "timed out" in str(exc).lower()
                or getattr(self, "offline_mode", False)
            )
            if is_net_error and self.storage is not None and (a.kind.startswith("rate_") or a.kind == "dismiss"):  # type: ignore[attr-defined]
                with self.lock:  # type: ignore[attr-defined]
                    a.status = "queued_offline"
                self.storage.queue_offline_review(  # type: ignore[attr-defined]
                    source=a.promo.source,
                    promo_key=a.promo.promo_key,
                    release_id=a.promo.release_id,
                    decision=a.kind,
                    rating=a.rating,
                    comment=a.comment,
                    favourite_track_id=a.track.inflyte_track_id if a.track else None,
                    promo_name=a.promo.name,
                    payload={"promo_name": a.promo.name, "rating": a.rating, "comment": a.comment},
                    media_paths=self._promo_media_paths(a.promo),
                )
                for tr in a.promo.tracks:
                    sig = track_signature(tr.artist_name or a.promo.name, tr.name)
                    if sig:
                        self.storage.record_track_signature(  # type: ignore[attr-defined]
                            signature=sig,
                            artist=tr.artist_name or a.promo.name,
                            title=tr.name,
                            decision=a.kind,
                            rating=a.rating,
                            source=a.promo.source,
                            promo_name=a.promo.name,
                        )
                self.notify("info", f"✈️ Offline: Queued “{a.promo.name}” for sync when reconnected")  # type: ignore[attr-defined]
            else:
                with self.lock:  # type: ignore[attr-defined]
                    a.status = "failed"
                    if a in self.history:  # type: ignore[attr-defined]
                        self.history.remove(a)  # type: ignore[attr-defined]
                    self.counts[a.count_key] = max(0, self.counts.get(a.count_key, 0) - 1)  # type: ignore[attr-defined]
                    self.promos.insert(min(1, len(self.promos)), a.promo)  # type: ignore[attr-defined]
                self.notify("error", f"Promo action failed for “{a.promo.name}”: {exc}")  # type: ignore[attr-defined]
                return
        self._changed()  # type: ignore[attr-defined]

    def undo_last(self) -> Optional[InflyteAction]:
        """Undo stepping to the next track or skipping a promo."""
        with self.lock:  # type: ignore[attr-defined]
            if not self.history:  # type: ignore[attr-defined]
                return None
            a = self.history[-1]  # type: ignore[attr-defined]
            if (a.kind.startswith("rate_") or a.kind == "dismiss") and a.status == "done":
                raise SessionError("Feedback/dismissal was already sent to Inflyte and cannot be undone")
            self.history.pop()  # type: ignore[attr-defined]
            a.undo_requested = True
            if a.kind == "next_track":
                self.current_track_idx = a.prev_track_idx  # type: ignore[attr-defined]
            else:
                self.promos.insert(0, a.promo)  # type: ignore[attr-defined]
                self.current_track_idx = a.prev_track_idx  # type: ignore[attr-defined]
                if a.slot == SKIP_SLOT:
                    self.counts["skip"] = max(0, self.counts.get("skip", 0) - 1)  # type: ignore[attr-defined]
                else:
                    self.counts[a.count_key] = max(0, self.counts.get(a.count_key, 0) - 1)  # type: ignore[attr-defined]
        self._prefetch_nearby()  # type: ignore[attr-defined]
        self._changed()  # type: ignore[attr-defined]
        return a

    def undo_all(self) -> int:
        n = 0
        while True:
            try:
                if self.undo_last() is None:
                    break
                n += 1
            except SessionError:
                break
        return n
