import http.client
import pytest
from bakkis_sorter.server import parse_range
from .server_helpers import api, app, request


def test_parse_range():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=-500", 100) == (0, 99)
    assert parse_range("bytes=50-1000", 100) == (50, 99)
    assert parse_range("bytes=0-1,5-6", 100) is None
    assert parse_range("items=0-1", 100) is None
    for bad in ("bytes=100-", "bytes=5-2", "bytes=-0"):
        with pytest.raises(ValueError):
            parse_range(bad, 100)


# ------------------------------------------------------------------ security


def test_api_requires_header_token(app):
    _, server = app
    assert api(server, "state", token="wrong")[0] == 403
    r, _ = request(server, "POST", f"/api/state?t={server.token}", b"{}")
    assert r.status == 403  # query token is not enough for the API
    assert api(server, "state")[0] == 200


def test_rejects_foreign_host_header(app):
    _, server = app
    r, _ = request(server, "GET", "/", host="evil.example:80")
    assert r.status == 403
    r, _ = request(server, "GET", "/")
    assert r.status == 200


def test_static_blocks_traversal(app):
    _, server = app
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/../../tests/conftest.py"):
        r, _ = request(server, "GET", path)
        assert r.status == 404


def test_api_errors(app):
    _, server = app
    assert api(server, "nope")[0] == 404
    assert api(server, "sort", track_id="1", mode="swipe", slot=0)[0] == 409  # no folder loaded
    status, body = api(server, "load_folder", bogus=1)
    assert status == 400 and "Bad parameters" in body["error"]
    r, data = request(server, "POST", "/api/state", b"[1]", {"X-Bakkis-Token": server.token})
    assert r.status == 400
    r, data = request(server, "POST", "/api/state", b"{bad", {"X-Bakkis-Token": server.token})
    assert r.status == 400


# ------------------------------------------------------------------ audio / cover


def test_rejected_post_does_not_poison_keep_alive_connection(app):
    _, server = app
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("POST", "/api/state", body=b"{}", headers={"X-Bakkis-Token": "wrong"})
    r = conn.getresponse()
    r.read()
    assert r.status == 403
    conn.request("GET", "/static/app.js")  # same connection
    r = conn.getresponse()
    assert r.status == 200 and b"BAKKIS" in r.read()
    conn.close()

