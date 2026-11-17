"""Platform, AppKit, and pywebview window constants for BAKKIS MUSIC SORTER.

Centralizes Cocoa / AppKit window style masks, collection behaviors, and pywebview
dialog identifiers away from application business logic.
"""

from __future__ import annotations

import ctypes

# pywebview FileDialog identifier for folder selection
FOLDER_DIALOG: int = 20  # webview.FileDialog.FOLDER

# macOS AppKit / NSWindow style masks & collection behaviors
NS_FULLSCREEN_MASK: int = 1 << 14  # NSWindowStyleMaskFullScreen
NS_FULLSCREEN_PRIMARY: int = 1 << 7  # NSWindowCollectionBehaviorFullScreenPrimary

# Default UI geometry & theme
DEFAULT_WINDOW_SIZE: tuple[int, int] = (1280, 860)
MIN_WINDOW_SIZE: tuple[int, int] = (960, 680)
BACKGROUND_COLOR: str = "#0E0F11"

_QOS_CLASS_BACKGROUND = 0x09


def set_background_thread_qos() -> None:
    """Hint macOS scheduler to run only the calling thread on efficiency cores (QoS Background)."""
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        fn = getattr(libc, "pthread_set_qos_class_self_np", None)
        if fn is not None:
            fn(ctypes.c_uint(_QOS_CLASS_BACKGROUND), ctypes.c_int(0))
    except Exception:
        pass
