"""Data models and identifiers for unified Inflyte & Trackstack promo reviewer."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..session import SKIP_SLOT

_PROMO_TRACK_IDS = itertools.count(100_000)
_INFLYTE_TRACK_IDS = _PROMO_TRACK_IDS


@dataclass
class PromoTrack:
    """A single track within a promo release (Inflyte release or Trackstack submission)."""

    id: str  # process-unique ID used by BAKKIS UI & /audio/<id>
    inflyte_track_id: int
    release_id: int
    name: str
    artist_name: str
    duration: float
    track_index: int  # 0-based index within the promo
    track_total: int  # total tracks in the promo
    peak_url: str = ""
    preview_url: Optional[str] = None
    cached_audio_path: Optional[Path] = None
    source: str = "inflyte"  # "inflyte" | "trackstack"
    submission_track_id: Optional[str] = None
    track_uuid: Optional[str] = None
    bpm: Optional[float] = None
    genre: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "inflyte_track_id": self.inflyte_track_id,
            "release_id": self.release_id,
            "name": self.name,
            "artist_name": self.artist_name,
            "duration": self.duration,
            "track_index": self.track_index,
            "track_total": self.track_total,
            "peak_url": self.peak_url,
            "preview_url": self.preview_url,
            "cached_audio_path": str(self.cached_audio_path) if self.cached_audio_path else None,
            "source": self.source,
            "submission_track_id": self.submission_track_id,
            "track_uuid": self.track_uuid,
            "bpm": self.bpm,
            "genre": self.genre,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromoTrack:
        cached = Path(d["cached_audio_path"]) if d.get("cached_audio_path") else None
        return cls(
            id=str(d.get("id") or ""),
            inflyte_track_id=int(d.get("inflyte_track_id") or 0),
            release_id=int(d.get("release_id") or 0),
            name=str(d.get("name") or ""),
            artist_name=str(d.get("artist_name") or ""),
            duration=float(d.get("duration") or 0.0),
            track_index=int(d.get("track_index") or 0),
            track_total=int(d.get("track_total") or 1),
            peak_url=str(d.get("peak_url") or ""),
            preview_url=d.get("preview_url"),
            cached_audio_path=cached,
            source=str(d.get("source") or "inflyte"),
            submission_track_id=d.get("submission_track_id"),
            track_uuid=d.get("track_uuid"),
            bpm=float(d["bpm"]) if d.get("bpm") is not None else None,
            genre=d.get("genre"),
        )


@dataclass
class PromoRelease:
    """A multi-track or single-track promo release (Inflyte release or Trackstack submission)."""

    release_id: int
    name: str
    sender_label: str
    release_date: str
    thumbnail_url: str
    full_image_url: str
    summary: str
    tracks: list[PromoTrack] = field(default_factory=list)
    cached_cover_path: Optional[Path] = None
    source: str = "inflyte"  # "inflyte" | "trackstack"
    submission_id: Optional[str] = None

    @property
    def promo_key(self) -> str:
        if self.source == "trackstack" and self.submission_id:
            return str(self.submission_id)
        return str(self.release_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "name": self.name,
            "sender_label": self.sender_label,
            "release_date": self.release_date,
            "thumbnail_url": self.thumbnail_url,
            "full_image_url": self.full_image_url,
            "summary": self.summary,
            "tracks": [t.to_dict() for t in self.tracks],
            "cached_cover_path": str(self.cached_cover_path) if self.cached_cover_path else None,
            "source": self.source,
            "submission_id": self.submission_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromoRelease:
        cached = Path(d["cached_cover_path"]) if d.get("cached_cover_path") else None
        tracks = [PromoTrack.from_dict(t) for t in d.get("tracks", [])]
        return cls(
            release_id=int(d.get("release_id") or 0),
            name=str(d.get("name") or ""),
            sender_label=str(d.get("sender_label") or ""),
            release_date=str(d.get("release_date") or ""),
            thumbnail_url=str(d.get("thumbnail_url") or ""),
            full_image_url=str(d.get("full_image_url") or ""),
            summary=str(d.get("summary") or ""),
            tracks=tracks,
            cached_cover_path=cached,
            source=str(d.get("source") or "inflyte"),
            submission_id=d.get("submission_id"),
        )


@dataclass
class PromoAction:
    """An action taken on a promo (swipe rating, next track, skip, or dismiss)."""

    id: int
    promo: PromoRelease
    track: PromoTrack
    prev_track_idx: int
    slot: int  # SKIP_SLOT (-1), 0 (Maybe / Keep), 1 (Not for me / Dismiss), 2 (Feeling it / Favorite)
    kind: str  # "next_track" | "skip_promo" | "rate_5" | "rate_4" | "rate_3" | "rate_2" | "rate_1" | "dismiss"
    label: str
    preset_id: str
    preset_name: str
    rating: Optional[int] = None
    comment: Optional[str] = None
    status: str = "pending"
    undo_requested: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def count_key(self) -> str:
        return "skip" if self.slot == SKIP_SLOT else f"{self.preset_id}:{self.slot}"

    def public(self) -> dict:
        suffix = f" ({self.rating}/5: “{self.comment}”)" if self.rating and self.comment else ""
        return {
            "id": self.id,
            "track_id": self.track.id,
            "name": f"{self.track.artist_name} – {self.track.name}" if self.track.artist_name else self.track.name,
            "label": f"{self.label}{suffix}",
            "mode": "swipe",
            "slot": self.slot,
            "op": "inflyte",
            "status": self.status,
            "dest": None,
        }


# Backwards compatibility aliases
InflyteTrack = PromoTrack
InflytePromo = PromoRelease
InflyteAction = PromoAction
