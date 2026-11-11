import errno
import pytest
from mutagen.id3 import APIC, ID3, TIT2
from bakkis_sorter import server as srv
from .audio_fixtures import PNG_1PX, make_mp3
from .server_helpers import api, app, load, request


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


def test_audio_write_retries_on_enobufs(monkeypatch):
    import errno

    from bakkis_sorter import server as srv

    class FakeWfile:
        def __init__(self, fails):
            self.fails, self.data = fails, b""

        def write(self, b):
            if self.fails:
                self.fails -= 1
                raise OSError(errno.ENOBUFS, "No buffer space available")
            self.data += b

    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    h = srv._Handler.__new__(srv._Handler)
    h.wfile = FakeWfile(3)
    h._write_retrying(b"abc")
    assert h.wfile.data == b"abc"
    h.wfile = FakeWfile(srv.WRITE_RETRIES)
    with pytest.raises(OSError):
        h._write_retrying(b"x")

