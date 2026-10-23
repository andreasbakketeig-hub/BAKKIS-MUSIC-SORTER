"""Sorting in steps: Genre Sort first, then Quick Sort inside each genre folder."""

from .audio_fixtures import make_mp3
from .test_server import api, app, load  # noqa: F401  (fixture + helpers)


def test_genre_then_quick_sort_inside_the_genre_folder(app, music, monkeypatch):
    backend, server = app
    for n in ("a.mp3", "b.mp3", "c.mp3"):
        make_mp3(music / n)
    api(server, "set_settings", file_operation="copy", track_order="in_order")
    sess = load(server, music)["session"]
    assert sess["operation"] == "copy" and not sess["in_place"]
    for _ in range(3):
        cur = api(server, "state")[1]["result"]["session"]["current"]
        api(server, "sort", track_id=cur["id"], mode="grid", slot=6)  # Techno
    backend.session.flush()
    techno = music.resolve() / "BAKKIS_SORTED_COPIES" / "Genre Sort" / "Techno"
    assert sorted(p.name for p in techno.iterdir()) == ["a.mp3", "b.mp3", "c.mp3"]

    # Second pass: the copies are ours, so they are moved, into the genre folder itself
    sess = load(server, techno)["session"]
    assert sess["total"] == 3 and sess["operation"] == "move" and sess["in_place"]
    api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    backend.session.flush()
    assert (techno / "Quick Sort" / "Keep" / "a.mp3").exists() and not (techno / "a.mp3").exists()
    assert (music / "a.mp3").exists()  # the original is never touched

    # Loading the genre folder again does not pick up the Quick Sort output
    again = load(server, techno)["session"]
    assert again["total"] == 2
    opened = []
    monkeypatch.setattr(backend, "_open_in_finder", opened.append)
    assert api(server, "reveal")[1]["result"] == str(techno)
    names = [s["label"] for s in api(server, "listen_sources")[1]["result"] if s["key"].startswith("copies:")]
    assert "Keep · Genre Sort · Techno · Quick Sort (copies)" in names


def test_support_note_once_after_250_sorts(app, music, monkeypatch):
    from bakkis_sorter import api as api_mod
    from bakkis_sorter.config import default_settings, load_settings

    assert default_settings()["cleanup_empty_source_folders_after_sort"] is True
    assert default_settings()["create_dated_sorting_sessions"] is False
    backend, server = app
    monkeypatch.setattr(api_mod, "SUPPORT_NUDGE_AT", 2)
    for n in ("a.mp3", "b.mp3", "c.mp3"):
        make_mp3(music / n)
    sess = load(server, music)["session"]
    flags = []
    for _ in range(3):
        cur = api(server, "state")[1]["result"]["session"]["current"]
        flags.append(api(server, "sort", track_id=cur["id"], mode="swipe", slot=0)[1]["result"].get("support_nudge"))
    assert flags == [None, 2, None]
    assert load_settings()["support_nudged"] is True and backend.settings["sorted_total"] == 3


def test_close_right_after_a_sort_does_not_hang(app, music):
    import threading

    backend, server = app
    make_mp3(music / "a.mp3")
    sess = load(server, music)["session"]
    api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    t = threading.Thread(target=backend.close, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()  # the session's worker shares the backend lock; close must not hold it
    assert (music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep" / "a.mp3").exists()
