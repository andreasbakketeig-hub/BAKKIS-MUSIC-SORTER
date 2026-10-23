"""Desktop app: a pywebview (WKWebView) window around the local server.

Native actions the web UI cannot do itself are registered on the backend as API methods:
``choose_folder``, ``toggle_fullscreen`` and ``exit_fullscreen``. Folder drag-and-drop is received
here (pywebview only exposes full paths to Python) and handed to the UI's ``loadFolder``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME
from .api import AppBackend
from .config import migrate_from_v1
from .library import is_audio_path
from .server import AppServer

FOLDER_DIALOG = 20  # webview.FileDialog.FOLDER
NS_FULLSCREEN_MASK = 1 << 14  # NSWindowStyleMaskFullScreen
NS_FULLSCREEN_PRIMARY = 1 << 7  # NSWindowCollectionBehaviorFullScreenPrimary
WINDOW_SIZE = (1280, 860)
MIN_SIZE = (960, 680)
BACKGROUND = "#0E0F11"


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

    # ------------------------------------------------------------------ API methods

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


def main() -> int:
    import webview

    debug = bool(os.environ.get("BAKKIS_DEBUG"))
    migrate_from_v1()
    backend = AppBackend()
    server = AppServer(backend).start()
    if debug:
        print(f"BAKKIS MUSIC SORTER server: {server.url}", flush=True)
    window = webview.create_window(
        APP_NAME,
        server.url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_SIZE,
        background_color=BACKGROUND,
        text_select=False,
    )
    bridge = NativeBridge(window, backend)
    bridge.register()
    window.events.loaded += bridge.on_loaded
    try:
        webview.start(private_mode=True, debug=debug)
    finally:
        # Let queued file operations finish before the process exits.
        server.stop()
        backend.close()
    return 0
