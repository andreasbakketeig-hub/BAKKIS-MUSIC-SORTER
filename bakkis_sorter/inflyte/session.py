"""Backwards-compatibility shim for bakkis_sorter.inflyte.session."""

from ..promos.session import InflyteSession, PromoSession

__all__ = ["InflyteSession", "PromoSession"]
