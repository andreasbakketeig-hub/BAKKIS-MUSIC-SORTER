"""Session management for Inflyte & Trackstack promo box reviewer."""

from __future__ import annotations

from .actions import SessionActionsMixin
from .core import SessionCoreMixin
from .offline import SessionOfflineMixin
from .stash import SessionStashMixin


class InflyteSession(
    SessionCoreMixin,
    SessionActionsMixin,
    SessionStashMixin,
    SessionOfflineMixin,
):
    """Manages the unified queue of unreviewed Inflyte & Trackstack promos with multi-track skip and auto-feedback."""

    pass


# Backwards compatibility alias
PromoSession = InflyteSession

__all__ = ["InflyteSession", "PromoSession"]
