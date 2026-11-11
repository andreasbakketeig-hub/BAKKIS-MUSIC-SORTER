"""Desktop app: a pywebview (WKWebView) window around the local server.

Native actions the web UI cannot do itself are registered on the backend as API methods:
``choose_folder``, ``toggle_fullscreen``, ``exit_fullscreen`` and ``quit_app`` (used by the updater). Folder drag-and-drop is received
here (pywebview only exposes full paths to Python) and handed to the UI's ``loadFolder``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME
from .api import AppBackend
from .config import bundle_dir, migrate_from_v1
from .library import is_audio_path
from .platform import (
    BACKGROUND_COLOR,
    DEFAULT_WINDOW_SIZE,
    FOLDER_DIALOG,
    MIN_WINDOW_SIZE,
    NS_FULLSCREEN_MASK,
    NS_FULLSCREEN_PRIMARY,
)
from .server import AppServer
from .storage import get_storage
from .types import WindowLayout


def folder_from_drop(paths: list[str]) -> Optional[str]:
    """Pick the folder to load from dropped paths: the first folder, else the parent of the first audio file."""
    for p in paths:
        if p and Path(p).is_dir():
            return str(Path(p))
    for p in paths:
        if p and Path(p).is_file() and is_audio_path(Path(p)):
            return str(Path(p).parent)
    return None


class NativeBridge:
    """Window-level actions. ``window`` is a ``webview.Window`` (or a test double)."""

    def __init__(self, window: Any, backend: AppBackend) -> None:
        self.window = window
        self.backend = backend
        self.fullscreen = False
        self._drop_registered = False
        self._lock = threading.Lock()

    def register(self) -> None:
        self.backend.register_native("choose_folder", self.choose_folder)
        self.backend.register_native("toggle_fullscreen", self.toggle_fullscreen)
        self.backend.register_native("exit_fullscreen", self.exit_fullscreen)
        self.backend.register_native("quit_app", self.quit_app)

    def save_window_layout(self) -> None:
        """Persist window geometry and fullscreen state to bakkis.db kv_store."""
        try:
            native = getattr(self.window, "native", None)
            is_fs = self.is_fullscreen()
            if native is not None:
                frame = native.frame()
                w = int(frame.size.width)
                h = int(frame.size.height)
                x = int(frame.origin.x)
                y = int(frame.origin.y)
            else:
                w = int(getattr(self.window, "width", DEFAULT_WINDOW_SIZE[0]))
                h = int(getattr(self.window, "height", DEFAULT_WINDOW_SIZE[1]))
                x = int(getattr(self.window, "x", 0)) if getattr(self.window, "x", None) is not None else None
                y = int(getattr(self.window, "y", 0)) if getattr(self.window, "y", None) is not None else None

            # Only save reasonable non-zero dimensions
            if w >= MIN_WINDOW_SIZE[0] and h >= MIN_WINDOW_SIZE[1]:
                layout = WindowLayout(
                    width=w,
                    height=h,
                    x=x,
                    y=y,
                    is_fullscreen=is_fs,
                )
                get_storage().put_kv("window_layout", layout.to_dict())
        except Exception:
            pass

    # ------------------------------------------------------------------ API methods

    def quit_app(self) -> None:
        """Close the window; main() then lets pending file operations finish before the process exits."""
        self.save_window_layout()
        self.window.destroy()

    def choose_folder(self) -> Optional[str]:
        start = self.backend.settings.get("default_folder_path") or str(Path.home() / "Music")
        result = self.window.create_file_dialog(FOLDER_DIALOG, directory=start if os.path.isdir(start) else "")
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    def toggle_fullscreen(self) -> bool:
        with self._lock:
            target = not self.is_fullscreen()
            self._toggle()
            self.fullscreen = target
            return target

    def exit_fullscreen(self) -> bool:
        with self._lock:
            if self.is_fullscreen():
                self._toggle()
            self.fullscreen = False
            return False

    def is_fullscreen(self) -> bool:
        """The window's real state (also covers the green button and ⌃⌘F), else our own flag."""
        native = getattr(self.window, "native", None)
        try:
            return bool(native.styleMask() & NS_FULLSCREEN_MASK)
        except Exception:  # noqa: BLE001 - no native window (tests) or not created yet
            return self.fullscreen

    def _toggle(self) -> None:
        native = getattr(self.window, "native", None)
        if native is None:
            self.window.toggle_fullscreen()
            return
        # pywebview's toggle_fullscreen switches the window to "managed" before leaving fullscreen,
        # which makes macOS ignore the request. Drive NSWindow directly on the main thread instead.
        from PyObjCTools import AppHelper

        def go() -> None:
            native.setCollectionBehavior_(native.collectionBehavior() | NS_FULLSCREEN_PRIMARY)
            native.toggleFullScreen_(None)

        AppHelper.callAfter(go)

    # ------------------------------------------------------------------ drag and drop

    def on_loaded(self) -> None:
        """Register the drop handler once the DOM exists (only once; the page is never reloaded)."""
        if self._drop_registered:
            return
        from webview.dom import DOMEventHandler

        self.window.dom.document.events.drop += DOMEventHandler(self.on_drop, prevent_default=True, stop_propagation=False)
        self._drop_registered = True

    def on_drop(self, event: dict) -> None:
        files = ((event or {}).get("dataTransfer") or {}).get("files") or []
        folder = folder_from_drop([f.get("pywebviewFullPath") for f in files if isinstance(f, dict)])
        if folder is None:
            self.window.evaluate_js('toast("Drop a folder (or a track inside one) to load it", { level: "warning" })')
            return
        self.window.evaluate_js(f"loadFolder({json.dumps(folder)})")


def set_dock_icon() -> None:
    """Running from source, macOS shows the Python rocket; use our icon instead (the .app has its own)."""
    if getattr(sys, "frozen", False):
        return
    icon = bundle_dir() / "assets" / "icon" / "icon-1024.png"
    try:
        import AppKit

        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def main() -> int:
    import webview

    debug = bool(os.environ.get("BAKKIS_DEBUG"))
    migrate_from_v1()
    backend = AppBackend()
    server = AppServer(backend).start()
    try:
        from .config import config_dir

        (config_dir() / "server_url.txt").write_text(server.url, encoding="utf-8")
    except Exception:
        pass
    if debug:
        print(f"BAKKIS MUSIC SORTER server: {server.url}", flush=True)

    storage = get_storage()
    layout = WindowLayout.from_dict(storage.get_kv("window_layout"))
    init_w = layout.width if layout else DEFAULT_WINDOW_SIZE[0]
    init_h = layout.height if layout else DEFAULT_WINDOW_SIZE[1]
    init_w = max(MIN_WINDOW_SIZE[0], init_w)
    init_h = max(MIN_WINDOW_SIZE[1], init_h)
    init_x = layout.x if layout else None
    init_y = layout.y if layout else None
    is_fs = layout.is_fullscreen if layout else False

    window = webview.create_window(
        APP_NAME,
        server.url,
        width=init_w,
        height=init_h,
        x=init_x if isinstance(init_x, (int, float)) else None,
        y=init_y if isinstance(init_y, (int, float)) else None,
        min_size=MIN_WINDOW_SIZE,
        background_color=BACKGROUND_COLOR,
        text_select=False,
        fullscreen=is_fs,
    )
    bridge = NativeBridge(window, backend)
    bridge.register()
    window.events.loaded += bridge.on_loaded
    window.events.closed += bridge.save_window_layout
    set_dock_icon()
    try:
        webview.start(private_mode=False, debug=debug)
    finally:
        bridge.save_window_layout()
        # Let queued file operations finish before the process exits.
        server.stop()
        backend.close()
    return 0
