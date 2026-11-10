"""Trackstack integration subpackage for BAKKIS MUSIC SORTER."""

from .bridge import TrackstackBridge
from .client import (
    TRACKSTACK_APP_URL,
    TRACKSTACK_DEFAULT_API_URL,
    TRACKSTACK_STATIC_BASE_URL,
    TRACKSTACK_STREAM_BASE_URL,
)
from .downloader import (
    DEFAULT_PROMO_DOWNLOAD_DIR,
    download_presigned_file,
    promo_download_dir,
    split_display_title,
    stable_int_id,
    trackstack_cache_dir,
    unique_download_dest,
)

__all__ = [
    "DEFAULT_PROMO_DOWNLOAD_DIR",
    "TRACKSTACK_APP_URL",
    "TRACKSTACK_DEFAULT_API_URL",
    "TRACKSTACK_STATIC_BASE_URL",
    "TRACKSTACK_STREAM_BASE_URL",
    "TrackstackBridge",
    "download_presigned_file",
    "promo_download_dir",
    "split_display_title",
    "stable_int_id",
    "trackstack_cache_dir",
    "unique_download_dest",
]
