"""Local HTTP server for the web UI.

- Binds to 127.0.0.1 on a random port with a per-launch secret token.
- ``GET /`` and ``GET /static/*`` serve the UI from ``web/`` (no token needed; contains no data).
- ``GET|HEAD /audio/<track_id>?t=<token>`` streams audio with HTTP Range support.
- ``GET /cover/<track_id>?t=<token>`` returns embedded cover art.
- ``POST /api/<method>`` with header ``X-Bakkis-Token`` and a JSON object body calls the backend.
  The token is required as a header (not a query param) for the API so other origins cannot call it
  without a CORS preflight, which we never allow.

Dev mode: ``python -m bakkis_sorter.server --dev <folder>`` runs the backend without pywebview and
prints a URL to open in a browser.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

from .metadata import image_mime

WEB_DIR = Path(__file__).resolve().parent / "web"
CHUNK = 256 * 1024
MAX_BODY = 1024 * 1024

AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".alac": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = int(status)


class Backend(Protocol):
    def dispatch(self, method: str, params: dict) -> object: ...

    def audio_path(self, track_id: str) -> Optional[Path]: ...

    def cover(self, track_id: str) -> Optional[bytes]: ...


def parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """Return inclusive (start, end) for a single ``bytes=`` range.

    Returns None when there is no usable Range header (serve the whole file).
    Raises ValueError when the range is syntactically valid but unsatisfiable (416).
    """
    if not header:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None  # multi-range or garbage: ignore and send the full body
    a, b = m.groups()
    if a == "" and b == "":
        return None
    if a == "":
        n = int(b)
        if n == 0:
            raise ValueError("empty suffix range")
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


class _Handler(BaseHTTPRequestHandler):
    server: "AppServer"
    protocol_version = "HTTP/1.1"

    # Silence default stderr logging; WebKit makes many range requests.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    # ------------------------------------------------------------------ helpers

    def _query(self) -> dict:
        return parse_qs(urlsplit(self.path).query)

    def _route(self) -> str:
        return unquote(urlsplit(self.path).path)

    def _token_ok(self, value: Optional[str]) -> bool:
        return bool(value) and hmac.compare_digest(str(value), self.server.token)

    def _host_ok(self) -> bool:
        # Defends against DNS rebinding: only accept our own loopback host names.
        host = (self.headers.get("Host") or "").lower()
        return host in self.server.allowed_hosts

    def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", headers: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, status: int, message: str = "") -> None:
        self._send(status, (message or HTTPStatus(status).phrase).encode("utf-8"))

    # ------------------------------------------------------------------ verbs

    def do_GET(self) -> None:  # noqa: N802
        self._handle_get()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_get()

    def do_POST(self) -> None:  # noqa: N802
        # Read the body before any early return: an unread body would be parsed as the start of the
        # next request on this keep-alive connection (seen as "501 Unsupported method ('{}GET')").
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return self._error(HTTPStatus.BAD_REQUEST)
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        raw = self.rfile.read(length) if length else b""
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN)
        route = self._route()
        if not route.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND)
        if not self._token_ok(self.headers.get("X-Bakkis-Token")):
            return self._error(HTTPStatus.FORBIDDEN)
        try:
            params = json.loads(raw or b"{}")
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
        if not isinstance(params, dict):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object"})
        method = route[len("/api/") :]
        try:
            result = self.server.backend.dispatch(method, params)
        except ApiError as e:
            return self._json(e.status, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - report instead of dropping the connection
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})
        self._json(HTTPStatus.OK, {"result": result})

    def _handle_get(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN)
        route = self._route()
        if route == "/" or route == "/index.html":
            return self._static("index.html")
        if route.startswith("/static/"):
            return self._static(route[len("/static/") :])
        if route.startswith("/audio/") or route.startswith("/cover/"):
            if not self._token_ok((self._query().get("t") or [None])[0]):
                return self._error(HTTPStatus.FORBIDDEN)
            track_id = route.split("/", 2)[2]
            if route.startswith("/audio/"):
                return self._audio(track_id)
            return self._cover(track_id)
        self._error(HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------------ static

    def _static(self, rel: str) -> None:
        root = self.server.web_dir.resolve()
        try:
            path = (root / rel).resolve()
            path.relative_to(root)
        except (ValueError, OSError):
            return self._error(HTTPStatus.NOT_FOUND)
        if not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, path.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    # ------------------------------------------------------------------ media

    def _cover(self, track_id: str) -> None:
        data = self.server.backend.cover(track_id)
        if not data:
            return self._error(HTTPStatus.NOT_FOUND)
        self._send(HTTPStatus.OK, data, image_mime(data), {"Cache-Control": "private, max-age=600"})

    def _audio(self, track_id: str) -> None:
        path = self.server.backend.audio_path(track_id)
        if path is None:
            return self._error(HTTPStatus.NOT_FOUND)
        try:
            f = open(path, "rb")
        except OSError:
            return self._error(HTTPStatus.NOT_FOUND)
        with f:
            size = os.fstat(f.fileno()).st_size
            ctype = AUDIO_MIME.get(Path(path).suffix.lower(), "application/octet-stream")
            try:
                rng = parse_range(self.headers.get("Range"), size)
            except ValueError:
                return self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", ctype, {"Content-Range": f"bytes */{size}"})
            if rng is None:
                start, end, status = 0, size - 1, HTTPStatus.OK
            else:
                (start, end), status = rng, HTTPStatus.PARTIAL_CONTENT
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD" or length == 0:
                return
            f.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    buf = f.read(min(CHUNK, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
            except (BrokenPipeError, ConnectionResetError):
                # WebKit aborts range requests all the time when seeking or preloading.
                self.close_connection = True


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, backend: Backend, *, port: int = 0, token: Optional[str] = None, web_dir: Path = WEB_DIR) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.backend = backend
        self.token = token or secrets.token_urlsafe(24)
        self.web_dir = Path(web_dir)
        p = self.server_address[1]
        self.allowed_hosts = frozenset({f"127.0.0.1:{p}", f"localhost:{p}"})
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> "AppServer":
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, name="bakkis-http", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def main(argv: Optional[list] = None) -> int:
    import argparse
    import time

    from .api import AppBackend
    from .config import migrate_from_v1

    ap = argparse.ArgumentParser(description="Run the BAKKIS MUSIC SORTER backend without the app window.")
    ap.add_argument("--dev", metavar="FOLDER", help="music folder to load (use a TEST folder)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    migrate_from_v1()
    backend = AppBackend()
    server = AppServer(backend, port=args.port).start()
    if args.dev:
        backend.dispatch("load_folder", {"path": args.dev})
    print(f"BAKKIS MUSIC SORTER dev server: {server.url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
