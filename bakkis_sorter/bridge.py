"""Base pywebview / WKWebView bridge providing common window lifecycle management.

Handles thread-safe window creation, AppKit window ordering to the foreground,
closed-event hook cleanup, and asynchronous JS evaluation with timeouts.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from .session import SessionError


class BaseWebViewBridge:
    """Base class for WebKit / pywebview child windows (Inflyte, Trackstack)."""

    def __init__(self, *, window_title: str, url: str, width: int = 1040, height: int = 740) -> None:
        self.window_title = window_title
        self.url = url
        self.width = width
        self.height = height

        self._lock = threading.RLock()
        self._win: Any = None
        self._loaded_event = threading.Event()
        self._login_window_shown: bool = False
        self._is_connected: bool = False

    def _on_window_closed(self) -> None:
        with self._lock:
            self._win = None
            self._login_window_shown = False
            self._loaded_event.clear()

    def _bring_to_front(self, win: Any) -> None:
        try:
            self._login_window_shown = True
            win.show()
            native = getattr(win, "native", None)
            if native is not None:
                from PyObjCTools import AppHelper

                def front() -> None:
                    native.makeKeyAndOrderFront_(None)
                    native.orderFrontRegardless()

                AppHelper.callAfter(front)
        except Exception:
            pass

    def _ensure_window(self, *, visible: bool = False) -> Any:
        with self._lock:
            if self._win is not None:
                if visible:
                    self._bring_to_front(self._win)
                return self._win
            try:
                import webview
            except ImportError as exc:
                raise SessionError("pywebview is required for bridge window") from exc

            self._loaded_event.clear()
            win = webview.create_window(
                self.window_title,
                self.url,
                width=self.width,
                height=self.height,
                hidden=not visible,
                focus=visible,
            )
            self._login_window_shown = visible
            win.events.loaded += lambda: self._loaded_event.set()
            try:
                win.events.closed += self._on_window_closed
            except Exception:
                pass
            self._win = win
        self._loaded_event.wait(timeout=8.0)
        return self._win

    def _eval_js(self, script: str, timeout: float = 15.0, *, visible: bool = False) -> Any:
        win = self._ensure_window(visible=visible)
        result_box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                result_box["value"] = win.evaluate_js(script)
            except Exception as exc:  # noqa: BLE001
                result_box["error"] = exc
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        if not done.wait(timeout=timeout):
            raise SessionError(f"Timed out communicating with {self.window_title}")
        if "error" in result_box:
            with self._lock:
                self._win = None
                self._login_window_shown = False
            raise SessionError(f"{self.window_title} bridge error: {result_box['error']}")
        return result_box.get("value")
