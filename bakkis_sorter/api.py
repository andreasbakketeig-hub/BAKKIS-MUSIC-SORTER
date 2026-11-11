"""Backend facade exposed to the web UI as ``POST /api/<method>``.

Backwards-compatibility re-export shim pointing to modular domain mixins in ``bakkis_sorter.backend``.
"""

from __future__ import annotations

from .backend import (
    AppBackend,
    DiagnosticsApiMixin,
    LibraryApiMixin,
    OPEN_URL_HOSTS,
    OPEN_URL_MAIL,
    PREFETCH_AHEAD,
    PromoApiMixin,
    SAVE_TOTAL_EVERY,
    SUPPORT_NUDGE_AT,
    SettingsApiMixin,
)

__all__ = [
    "AppBackend",
    "DiagnosticsApiMixin",
    "LibraryApiMixin",
    "OPEN_URL_HOSTS",
    "OPEN_URL_MAIL",
    "PREFETCH_AHEAD",
    "PromoApiMixin",
    "SAVE_TOTAL_EVERY",
    "SUPPORT_NUDGE_AT",
    "SettingsApiMixin",
]
