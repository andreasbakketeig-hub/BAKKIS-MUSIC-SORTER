"""Base pywebview / WKWebView bridge providing common window lifecycle management.

Handles thread-safe window creation, AppKit window ordering to the foreground,
closed-event hook cleanup, and asynchronous JS evaluation with timeouts.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

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

    @staticmethod
    def _native_wkwebview(win: Any) -> Any:
        uid = getattr(win, "uid", None)
        if not uid:
            return None
        try:
            from webview.platforms import cocoa

            bv = cocoa.BrowserView.instances.get(uid)
            wk = getattr(bv, "webview", None) if bv is not None else None
            if wk is not None and hasattr(wk, "callAsyncJavaScript_arguments_inFrame_inContentWorld_completionHandler_"):
                return wk
        except Exception:
            pass
        return None

    def _eval_async_js(
        self,
        async_body: str,
        *,
        sync_fallback: str,
        args: Optional[dict[str, Any]] = None,
        timeout: float = 15.0,
        visible: bool = False,
    ) -> Any:
        """Evaluate async JS (e.g. ``await fetch(...)``) without blocking WKWebView's UI thread."""
        if "_eval_js" in self.__dict__:
            return self._eval_js(sync_fallback, timeout=timeout, visible=visible)

        win = self._ensure_window(visible=visible)
        wk = self._native_wkwebview(win)
        if wk is None:
            return self._eval_js(sync_fallback, timeout=timeout, visible=visible)

        try:
            import WebKit
            from PyObjCTools import AppHelper
        except ImportError:
            return self._eval_js(sync_fallback, timeout=timeout, visible=visible)

        result_box: dict[str, Any] = {}
        done = threading.Event()

        def handler(result: Any, error: Any) -> None:
            if error is not None:
                result_box["error"] = str(error)
            else:
                result_box["value"] = result
            done.set()

        def invoke() -> None:
            try:
                wk.callAsyncJavaScript_arguments_inFrame_inContentWorld_completionHandler_(
                    async_body,
                    args or {},
                    None,
                    WebKit.WKContentWorld.pageWorld(),
                    handler,
                )
            except Exception as exc:  # noqa: BLE001
                result_box["error"] = exc
                done.set()

        AppHelper.callAfter(invoke)
        if not done.wait(timeout=timeout):
            raise SessionError(f"Timed out communicating with {self.window_title}")
        if "error" in result_box:
            raise SessionError(f"{self.window_title} async JS error: {result_box['error']}")
        val = result_box.get("value")
        if isinstance(val, str):
            try:
                return json.loads(val)
            except ValueError:
                return val
        return val

    def _start_auth_watcher(
        self,
        *,
        interceptor_js: str,
        on_ready: Callable[[], None],
        timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> None:
        """Poll a login WKWebView window in the background and invoke ``on_ready`` once signed in."""

        def watch() -> None:
            deadline = time.time() + float(timeout)
            while time.time() < deadline:
                with self._lock:
                    if self._win is None:
                        return
                try:
                    st = self._eval_js(interceptor_js, timeout=3.0, visible=False) or {}
                    if isinstance(st, dict) and st.get("ready"):
                        on_ready()
                        return
                except Exception:
                    pass
                time.sleep(float(poll_interval))

        threading.Thread(target=watch, daemon=True, name=f"auth-watch-{self.window_title[:16]}").start()
