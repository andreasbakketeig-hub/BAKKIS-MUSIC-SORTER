import pytest
from mutagen.id3 import ID3, TIT2
from .audio_fixtures import make_mp3
from .server_helpers import api, app, load, request


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
    assert (music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep" / "a.mp3").exists()
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


def test_bpm_key_filter(app, music):
    backend, server = app
    spec = [("a.mp3", "124", "8A"), ("b.mp3", "128", "Am"), ("c.mp3", "122", "9A"), ("d.mp3", None, "8A"), ("e.mp3", "126", "3B"), ("f.mp3", "124", "8B")]
    from mutagen.id3 import TBPM, TKEY
    for name, bpm, key in spec:
        p = make_mp3(music / name)
        t = ID3()
        if bpm:
            t.add(TBPM(encoding=3, text=bpm))
        t.add(TKEY(encoding=3, text=key))
        t.save(str(p))
    api(server, "set_settings", track_order="in_order")
    sess = load(server, music)["session"]
    assert sess["current"]["name"] == "a.mp3"
    names = lambda: [t["name"] for t in api(server, "queue", limit=50)[1]["result"]["items"]]

    snap = api(server, "set_filter", bpm_min=123, bpm_max=127)[1]["result"]
    assert names() == ["e.mp3", "f.mp3"] and snap["held"] == 3 and snap["remaining"] == 6  # a stays current
    api(server, "set_filter", key="8A", compatible=True, order="bpm")
    assert names() == ["c.mp3", "f.mp3", "b.mp3", "d.mp3"]  # 9A/8B/8A(Am) by BPM, no-BPM last
    api(server, "set_filter", key="8A")
    assert names() == ["b.mp3", "d.mp3"]
    assert api(server, "set_filter", key="H#")[0] == 400
    snap = api(server, "clear_filter")[1]["result"]
    assert names() == ["b.mp3", "c.mp3", "d.mp3", "e.mp3", "f.mp3"] and snap["held"] == 0 and snap["filter"] is None


def test_diagnostics_and_vacuum_api(app):
    backend, server = app

    # 1. Fetch DB stats
    status, body = api(server, "get_db_stats")
    assert status == 200
    stats = body["result"]
    assert "file_size_bytes" in stats
    assert "counts" in stats

    # 2. Vacuum DB
    status, body = api(server, "vacuum_db")
    assert status == 200
    vac_res = body["result"]
    assert "before_bytes" in vac_res
    assert "after_bytes" in vac_res
    assert "stats" in vac_res

    # 3. Diagnostic bundle
    status, body = api(server, "get_diagnostic_bundle")
    assert status == 200
    bundle = body["result"]
    assert bundle["copied"] is True
    assert "BAKKIS MUSIC SORTER — Diagnostic Report" in bundle["report"]
    assert "Table Counts" in bundle["report"]


def test_close_session_api(app, music):
    backend, server = app
    load(server, music)
    assert backend.session is not None
    status, body = api(server, "close_session")
    assert status == 200
    assert body["result"] == {"ok": True}
    assert backend.session is None
    assert backend.scan is None


def test_promo_history_api(app):
    backend, server = app
    status, body = api(server, "promo_history")
    assert status == 200
    res = body["result"]
    assert res["ok"] is True
    assert "reviews" in res
    assert "total" in res



