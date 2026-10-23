import io
import json

from bakkis_sorter import updates


def fake(payload=None, exc=None):
    def opener(req, timeout):
        assert "BAKKIS" in req.get_header("User-agent")
        if exc:
            raise exc
        return io.BytesIO(json.dumps(payload).encode())

    return opener


def test_parse_version():
    assert updates.parse_version("v2.1.0") > updates.parse_version("2.0.0")
    assert updates.parse_version("2.0.0") > updates.parse_version("2.0.0.dev0")
    assert updates.parse_version("v2.0.0-rc1") < updates.parse_version("2.0.0")
    assert updates.parse_version("v10.0") > updates.parse_version("9.9.9")
    assert updates.parse_version("garbage") == (0, 0, 0, 0)


def test_check_newer_and_same():
    r = updates.check("2.0.0", opener=fake({"tag_name": "v2.1.0", "html_url": "https://github.com/x/releases/v2.1.0"}))
    assert r["newer"] and r["latest"] == "2.1.0" and r["url"].startswith("https://")
    assert not updates.check("2.1.0", opener=fake({"tag_name": "v2.1.0"}))["newer"]


def test_check_failures_are_quiet():
    assert "error" in updates.check(opener=fake(exc=OSError("offline")))
    assert "error" in updates.check(opener=fake({"message": "Not Found"}))
    assert "error" in updates.check(opener=fake({"tag_name": "v9.0.0", "prerelease": True}))
