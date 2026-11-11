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


