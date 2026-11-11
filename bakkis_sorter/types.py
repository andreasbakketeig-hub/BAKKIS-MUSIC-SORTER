"""Typed dataclasses and structures for BAKKIS MUSIC SORTER.

Provides immutable, strictly-typed data transfer objects (DTOs) for:
- Window geometry and layout persistence (WindowLayout)
- WebView authentication and connection states (TrackstackAuthState, InflyteAuthState)
- System notices and notifications (AppNotice)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class WindowLayout:
    """Window geometry, position coordinates, and fullscreen state."""

    width: int
    height: int
    x: Optional[int] = None
    y: Optional[int] = None
    is_fullscreen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional[WindowLayout]:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                width=int(data["width"]),
                height=int(data["height"]),
                x=int(data["x"]) if data.get("x") is not None else None,
                y=int(data["y"]) if data.get("y") is not None else None,
                is_fullscreen=bool(data.get("is_fullscreen", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class TrackstackAuthState:
    """Trackstack connection status, popup window state, and resolved context identifiers."""

    connected: bool
    window_open: bool
    workspace_id: Optional[str] = None
    inbox_id: Optional[str] = None
    user_id: Optional[str] = None
    api_base_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InflyteAuthState:
    """Inflyte connection status, popup window state, and current URL."""

    connected: bool
    window_open: bool
    href: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AppNotice:
    """Transient user notification emitted by sorting sessions."""

    seq: int
    level: Literal["info", "warning", "error"]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
