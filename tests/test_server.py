import http.client
import json
import time

import pytest
from mutagen.id3 import APIC, ID3, TIT2

from bakkis_sorter.api import AppBackend
from bakkis_sorter.server import AppServer, parse_range

from .audio_fixtures import PNG_1PX, make_mp3


@pytest.fixture
def app(music):
    backend = AppBackend()
    server = AppServer(backend).start()
    yield backend, server
    server.stop()
    backend.close()


def request(server, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    h = dict(headers or {})
    if host is not None:
        h["Host"] = host
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def api(server, method, token=None, **params):
    r, data = request(
        server,
        "POST",
        f"/api/{method}",
        json.dumps(params),
        {"X-Bakkis-Token": server.token if token is None else token, "Content-Type": "application/json"},
    )
    return r.status, json.loads(data) if data and r.getheader("Content-Type", "").startswith("application/json") else data


def load(server, folder):
    status, _ = api(server, "load_folder", path=str(folder))
    assert status == 200
    deadline = time.time() + 5
    while time.time() < deadline:
        _, body = api(server, "state")
        if body["result"]["scan"]["status"] == "ready":
            return body["result"]
        time.sleep(0.01)
    raise AssertionError("scan did not finish")


# ------------------------------------------------------------------ range parsing


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


def test_audio_range_and_token(app, music):
    _, server = app
    p = make_mp3(music / "a.mp3")
    size = p.stat().st_size
    state = load(server, music)
    tid = state["session"]["current"]["id"]

    assert request(server, "GET", f"/audio/{tid}")[0].status == 403
    assert request(server, "GET", f"/audio/{tid}?t=bad")[0].status == 403
    assert request(server, "GET", f"/audio/999999?t={server.token}")[0].status == 404

    r, data = request(server, "GET", f"/audio/{tid}?t={server.token}")
    assert r.status == 200 and data == p.read_bytes()
    assert r.getheader("Content-Type") == "audio/mpeg" and r.getheader("Accept-Ranges") == "bytes"

    r, data = request(server, "GET", f"/audio/{tid}?t={server.token}", headers={"Range": "bytes=100-199"})
    assert r.status == 206 and data == p.read_bytes()[100:200]
    assert r.getheader("Content-Range") == f"bytes 100-199/{size}"

    r, data = request(server, "GET", f"/audio/{tid}?t={server.token}", headers={"Range": "bytes=-4"})
    assert r.status == 206 and data == p.read_bytes()[-4:]

    r, _ = request(server, "GET", f"/audio/{tid}?t={server.token}", headers={"Range": f"bytes={size}-"})
    assert r.status == 416 and r.getheader("Content-Range") == f"bytes */{size}"

    r, data = request(server, "HEAD", f"/audio/{tid}?t={server.token}")
    assert r.status == 200 and data == b"" and r.getheader("Content-Length") == str(size)


def test_cover_and_meta(app, music):
    _, server = app
    p = make_mp3(music / "a.mp3")
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Hello"))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=PNG_1PX))
    tags.save(str(p))
    tid = load(server, music)["session"]["current"]["id"]
    r, data = request(server, "GET", f"/cover/{tid}?t={server.token}")
    assert r.status == 200 and data == PNG_1PX and r.getheader("Content-Type") == "image/png"
    status, body = api(server, "track_meta", track_id=tid)
    assert status == 200 and body["result"]["display"] == "Hello" and body["result"]["has_cover"]


# ------------------------------------------------------------------ end-to-end sort flow


def test_sort_undo_flow_over_http(app, music):
    backend, server = app
    for n in ("a.mp3", "b.mp3"):
        make_mp3(music / n)
    api(server, "set_settings", create_dated_sorting_sessions=False, track_order="in_order")
    state = load(server, music)
    sess = state["session"]
    assert sess["total"] == 2 and sess["current"]["name"] == "a.mp3"

    status, body = api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    assert status == 200 and body["result"]["current"]["name"] == "b.mp3"
    status, body = api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    assert status == 409  # stale id (double press)

    backend.session.flush()
    assert (music.resolve() / "BAKKIS_SORTED" / "DJ Sorting" / "Keep" / "a.mp3").exists()
    assert api(server, "folder_counts", mode="swipe")[1]["result"] == [1, 0, 0]

    status, body = api(server, "undo")
    assert body["result"]["current"]["name"] == "a.mp3"
    backend.session.flush()
    assert (music / "a.mp3").exists()


def test_copy_mode_hides_copied_on_rescan(app, music):
    backend, server = app
    make_mp3(music / "a.mp3")
    make_mp3(music / "b.mp3")
    api(server, "set_settings", file_operation="copy", create_dated_sorting_sessions=False, track_order="in_order")
    sess = load(server, music)["session"]
    api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    backend.session.flush()
    assert (music / "a.mp3").exists()
    again = load(server, music)["session"]
    assert again["total"] == 1 and again["current"]["name"] == "b.mp3"


def test_presets_and_settings_over_api(app):
    _, server = app
    status, body = api(server, "preset_create", mode="swipe", name="Promo", labels=["A", "B", "C"], colors=[])
    pid = body["result"]["id"]
    api(server, "preset_select", preset_id=pid)
    state = api(server, "state")[1]["result"]
    assert state["presets"]["active_swipe_preset_id"] == pid
    assert api(server, "preset_delete", preset_id="missing")[0] == 409
    assert api(server, "set_settings", volume=12)[1]["result"]["volume"] == 12


def test_queue_paging_and_reveal(app, music, monkeypatch):
    backend, server = app
    for i in range(5):
        make_mp3(music / f"{i}.mp3")
    api(server, "set_settings", track_order="in_order")
    load(server, music)
    page = api(server, "queue", offset=1, limit=2)[1]["result"]
    assert page["total"] == 4 and [t["name"] for t in page["items"]] == ["2.mp3", "3.mp3"]
    assert page["items"][0]["index"] == 1
    opened = []
    monkeypatch.setattr(backend, "_open_in_finder", opened.append)
    assert api(server, "reveal")[1]["result"] == str(music.resolve())  # BAKKIS_SORTED not created yet
    assert opened == [music.resolve()]
    state = api(server, "state")[1]["result"]
    assert state["default_folder_exists"] is False
    api(server, "set_settings", default_folder_path=str(music))
    assert api(server, "state")[1]["result"]["default_folder_exists"] is True


def test_open_url_only_https_and_mailto(app, monkeypatch):
    backend, server = app
    opened = []
    monkeypatch.setattr(backend, "_open_external", opened.append)
    assert api(server, "open_url", url="https://bakkis.com")[0] == 200
    assert api(server, "open_url", url="mailto:bakkis@bakkis.com?subject=Hi")[0] == 200
    for bad in ("file:///etc/passwd", "http://bakkis.com", "javascript:alert(1)", "https://", "/Applications/Calculator.app"):
        assert api(server, "open_url", url=bad)[0] == 400, bad
    assert opened == ["https://bakkis.com", "mailto:bakkis@bakkis.com?subject=Hi"]
    about = api(server, "about")[1]["result"]
    assert about["version"] and about["macos"]
