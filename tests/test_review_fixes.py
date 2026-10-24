"""Regression tests for the findings of the external code review (2.0.3)."""

import threading
import time

from bakkis_sorter import library
from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session, SessionError, Worker

from .audio_fixtures import make_mp3
from .conftest import touch
from .test_server import api, app, load  # noqa: F401  (fixture + helpers)


def test_reserve_destination_is_exclusive(tmp_path):
    touch(tmp_path / "a.mp3", b"keep me")
    first = library.reserve_destination(tmp_path, "a.mp3")
    second = library.reserve_destination(tmp_path, "a.mp3")
    assert (first.name, second.name) == ("a (2).mp3", "a (3).mp3")
    assert first.exists() and first.stat().st_size == 0  # placeholder, replaced by the move
    assert (tmp_path / "a.mp3").read_bytes() == b"keep me"


def test_worker_survives_a_failing_job_and_flush_times_out():
    errors, ran = [], []
    w = Worker(on_error=errors.append)
    w.submit(lambda: 1 / 0)
    w.submit(lambda: ran.append(1))
    assert w.flush(5) and ran == [1] and isinstance(errors[0], ZeroDivisionError)
    gate = threading.Event()
    w.submit(gate.wait)
    assert w.flush(0.1) is False  # a stuck job (dead network disk) can't block forever
    gate.set()
    w.stop()


def make(music, paths, tmp_path, **settings):
    s = dict(default_settings(), **settings)
    return Session(music, paths, presets=PresetStore(), settings=lambda: s, journal=Journal(tmp_path / "j"))


def test_tracks_outside_the_folder_are_refused(music, tmp_path):
    outside = touch(tmp_path / "elsewhere" / "x.mp3")
    s = make(music, [outside], tmp_path)
    try:
        try:
            s.sort(s.current.id, "swipe", 0)
            raise AssertionError("should refuse")
        except SessionError:
            pass
        assert outside.exists()
    finally:
        s.close()


def test_copy_undo_leaves_a_replaced_copy_alone(music, tmp_path):
    a = touch(music / "a.mp3", b"original")
    s = make(music, [a], tmp_path, file_operation="copy")
    try:
        s.sort(s.current.id, "swipe", 0)
        s.flush()
        copy = s.history[-1].dest
        copy.write_bytes(b"someone else's longer file")
        s.undo_last()
        s.flush()
        assert copy.read_bytes() == b"someone else's longer file" and a.exists()
    finally:
        s.close()


def test_rescan_waits_for_pending_moves(app, music, monkeypatch):
    from bakkis_sorter import session as session_mod

    backend, server = app
    for n in ("a.mp3", "b.mp3"):
        make_mp3(music / n)
    api(server, "set_settings", track_order="in_order", move_warning_acknowledged=True)
    sess = load(server, music)["session"]
    real_move = session_mod._move

    def slow_move(src, dest):
        time.sleep(0.3)
        real_move(src, dest)

    monkeypatch.setattr(session_mod, "_move", slow_move)
    api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)
    again = load(server, music)["session"]  # reload while a.mp3 is still being moved
    assert again["total"] == 1 and again["current"]["name"] == "b.mp3"
    assert (music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep" / "a.mp3").exists()


def test_no_sorting_while_a_folder_loads_and_none_after_close(app, music):
    backend, server = app
    make_mp3(music / "a.mp3")
    sess = load(server, music)["session"]
    backend.scan["status"] = "scanning"
    assert api(server, "sort", track_id=sess["current"]["id"], mode="swipe", slot=0)[0] == 409
    backend.scan["status"] = "ready"
    backend.close()
    assert api(server, "state")[0] == 503


def test_open_url_allowlist(app, monkeypatch):
    backend, server = app
    opened = []
    monkeypatch.setattr(backend, "_open_external", opened.append)
    assert api(server, "open_url", url="https://ko-fi.com/bakkis")[0] == 200
    assert api(server, "open_url", url="https://open.spotify.com/artist/x")[0] == 200
    for bad in ("https://evil.example.com", "https://bakkis.com.evil.net/", "mailto:someone@else.com"):
        assert api(server, "open_url", url=bad)[0] == 400, bad
    assert len(opened) == 2


def test_no_empty_placeholder_when_the_source_is_gone(music, tmp_path):
    a = touch(music / "Sub" / "a.mp3", b"audio")
    s = make(music, [a], tmp_path)
    try:
        a.unlink()  # removed in Finder before the sort runs
        s.sort(s.current.id, "swipe", 0)
        s.flush()
        assert not any((music / "BAKKIS_SORTED").rglob("a*.mp3")) if (music / "BAKKIS_SORTED").exists() else True
    finally:
        s.close()


def test_no_empty_placeholder_when_the_sorted_file_is_gone(music, tmp_path):
    a = touch(music / "Sub" / "a.mp3", b"audio")
    s = make(music, [a], tmp_path)
    try:
        s.sort(s.current.id, "swipe", 0)
        s.flush()
        s.history[-1].dest.unlink()  # moved away in Finder (or by a later pass)
        s.undo_last()
        s.flush()
        assert not a.exists() and not list((music / "Sub").glob("a*.mp3")) if (music / "Sub").exists() else True
        assert s.notices_since(0)[-1]["level"] == "error"
    finally:
        s.close()
