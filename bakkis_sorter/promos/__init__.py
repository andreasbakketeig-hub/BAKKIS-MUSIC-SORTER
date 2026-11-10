"""Unified promo management package for BAKKIS MUSIC SORTER (Inflyte + Trackstack)."""

from .comments import CommentPool, default_comments_path, parse_comments_text
from .inflyte import INFLYTE_BASE_URL, INFLYTE_PROMOS_URL, InflyteBridge, cache_dir
from .models import (
    InflyteAction,
    InflytePromo,
    InflyteTrack,
    PromoAction,
    PromoRelease,
    PromoTrack,
    _INFLYTE_TRACK_IDS,
)
from .session import InflyteSession, PromoSession
from .trackstack import (
    DEFAULT_PROMO_DOWNLOAD_DIR,
    TRACKSTACK_APP_URL,
    TRACKSTACK_DEFAULT_API_URL,
    TRACKSTACK_STATIC_BASE_URL,
    TRACKSTACK_STREAM_BASE_URL,
    TrackstackBridge,
    download_presigned_file,
    promo_download_dir,
    split_display_title,
    stable_int_id,
    trackstack_cache_dir,
    unique_download_dest,
)

__all__ = [
    "CommentPool",
    "DEFAULT_PROMO_DOWNLOAD_DIR",
    "INFLYTE_BASE_URL",
    "INFLYTE_PROMOS_URL",
    "InflyteAction",
    "InflyteBridge",
    "InflytePromo",
    "InflyteSession",
    "InflyteTrack",
    "PromoAction",
    "PromoRelease",
    "PromoSession",
    "PromoTrack",
    "TRACKSTACK_APP_URL",
    "TRACKSTACK_DEFAULT_API_URL",
    "TRACKSTACK_STATIC_BASE_URL",
    "TRACKSTACK_STREAM_BASE_URL",
    "TrackstackBridge",
    "_INFLYTE_TRACK_IDS",
    "cache_dir",
    "default_comments_path",
    "download_presigned_file",
    "parse_comments_text",
    "promo_download_dir",
    "split_display_title",
    "stable_int_id",
    "trackstack_cache_dir",
    "unique_download_dest",
]
