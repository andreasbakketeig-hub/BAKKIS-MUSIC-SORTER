import json
import threading
from datetime import date

import pytest

from bakkis_sorter import session as session_mod
from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session, SessionError, filter_already_copied, load_copied_index

from .conftest import touch

DAY = date(2026, 10, 23)


@pytest.fixture
def settings():
    s = default_settings()
    s["create_dated_sorting_sessions"] = False
    return s


@pytest.fixture
def make_session(music, settings, tmp_path):
    created = []

    def make(paths, **kw):
        s = Session(
            music,
            paths,
            presets=PresetStore(),
            settings=lambda: settings,
            journal=Journal(tmp_path / "journal"),
            today=lambda: DAY,
            **kw,
        )
        created.append(s)
        return s

    yield make
    for s in created:
        s.close()


def keep_dir(music):
    return music.resolve() / "BAKKIS_SORTED" / "DJ Sorting" / "Keep"


def journal_lines(tmp_path):
    f = tmp_path / "journal" / f"{date.today().isoformat()}.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def tracks(music, *names):
    return [touch(music.resolve() / n, n.encode()) for n in names]


# ------------------------------------------------------------------ move


def test_sort_moves_file_and_advances(music, make_session, tmp_path):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    first = s.current.id
    s.sort(first, "swipe", 0)
    assert s.current.path == b  # optimistic: next track immediately
    s.flush()
    assert not a.exists()
    assert (keep_dir(music) / "a.mp3").read_bytes() == b"a.mp3"
    assert s.history[-1].status == "done"
    assert s.counts == {f"{s.presets.active('swipe')['id']}:0": 1}
    snap = s.snapshot()
    assert snap["position"] == 2 and snap["remaining"] == 1 and snap["history"][0]["label"] == "Keep"
    [entry] = journal_lines(tmp_path)
    assert entry["event"] == "sort" and entry["op"] == "move" and entry["dest"].endswith("Keep/a.mp3")


def test_dated_sessions_and_grid(music, make_session, settings):
    settings["create_dated_sorting_sessions"] = True
    (a,) = tracks(music, "a.mp3")
    s = make_session([a])
    s.sort(s.current.id, "grid", 6)
    s.flush()
    assert (music.resolve() / "BAKKIS_SORTED" / "Genre Sorting" / "2026-10-23" / "Techno" / "a.mp3").exists()


def test_skip_preserves_subfolders(music, make_session):
    (a,) = tracks(music, "Promos/Label/a.mp3")
    s = make_session([a])
    s.skip(s.current.id, "swipe")
    s.flush()
    assert (music.resolve() / "BAKKIS_SORTED" / "DJ Sorting" / "Skipped" / "Promos" / "Label" / "a.mp3").exists()


def test_stale_track_id_and_bad_slot_rejected(music, make_session):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    tid = s.current.id
    s.sort(tid, "swipe", 0)
    with pytest.raises(SessionError):
        s.sort(tid, "swipe", 0)  # double press with the old id
    with pytest.raises(SessionError):
        s.sort(s.current.id, "swipe", 3)
    s.sort(s.current.id, "swipe", 1)
    with pytest.raises(SessionError):
        s.skip("x", "swipe")  # empty queue


def test_name_collision(music, make_session):
    (a,) = tracks(music, "a.mp3")
    touch(keep_dir(music) / "a.mp3", b"old")
    s = make_session([a])
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert (keep_dir(music) / "a.mp3").read_bytes() == b"old"
    assert (keep_dir(music) / "a (2).mp3").read_bytes() == b"a.mp3"


# ------------------------------------------------------------------ undo


def test_undo_restores_file_and_queue(music, make_session, tmp_path):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    undone = s.undo_last()
    assert s.current.path == a and undone.track is s.current
    s.flush()
    assert a.read_bytes() == b"a.mp3"
    assert not (keep_dir(music) / "a.mp3").exists()
    assert s.counts[undone.count_key] == 0 and s.history == []
    assert [e["event"] for e in journal_lines(tmp_path)] == ["sort", "undo"]
    assert s.undo_last() is None


def test_undo_never_overwrites(music, make_session):
    (a,) = tracks(music, "a.mp3")
    s = make_session([a])
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    touch(a, b"new file with same name")
    s.undo_last()
    s.flush()
    assert a.read_bytes() == b"new file with same name"
    restored = music.resolve() / "a (2).mp3"
    assert restored.read_bytes() == b"a.mp3"
    assert s.current.path == restored
    assert any("restored as a (2).mp3" in n["message"] for n in s.notices_since(0))


def test_undo_all(music, make_session):
    paths = tracks(music, "a.mp3", "b.mp3", "c.mp3")
    s = make_session(list(paths))
    s.sort(s.current.id, "swipe", 0)
    s.skip(s.current.id, "swipe")
    s.sort(s.current.id, "grid", 0)
    assert s.undo_all() == 3
    s.flush()
    assert all(p.exists() for p in paths)
    assert [t.path for t in s.queue] == list(paths)
    assert sum(s.counts.values()) == 0


def test_undo_before_worker_runs_cancels_the_move(music, make_session, monkeypatch):
    gate = threading.Event()
    real_move = session_mod._move
    calls = []

    def slow_move(src, dest):
        calls.append((src, dest))
        real_move(src, dest)

    a, b, c = tracks(music, "a.mp3", "b.mp3", "c.mp3")
    s = make_session([a, b, c])
    s._worker.submit(gate.wait)  # block the worker
    monkeypatch.setattr(session_mod, "_move", slow_move)
    s.sort(s.current.id, "swipe", 0)
    s.undo_last()
    gate.set()
    s.flush()
    assert calls == [] and a.exists() and s.current.path == a


def test_cleanup_empty_source_folders_and_undo_recreates(music, make_session, settings):
    settings["cleanup_empty_source_folders_after_sort"] = True
    (a,) = tracks(music, "Promos/Label/EP/a.mp3")
    touch(music / "Promos" / "Label" / "EP" / ".DS_Store")
    touch(music / "Promos" / "other.mp3")
    s = make_session([a])
    s.sort(s.current.id, "swipe", 1)
    s.flush()
    assert not (music / "Promos" / "Label").exists()
    assert (music / "Promos" / "other.mp3").exists()
    assert len(s.history[-1].deleted_dirs) == 2
    s.undo_last()
    s.flush()
    assert a.read_bytes() == b"Promos/Label/EP/a.mp3"


# ------------------------------------------------------------------ failures


def test_failed_move_puts_track_back_as_next(music, make_session, monkeypatch, tmp_path):
    def broken(src, dest):
        raise PermissionError("disk is read-only")

    a, b, c = tracks(music, "a.mp3", "b.mp3", "c.mp3")
    s = make_session([a, b, c])
    monkeypatch.setattr(session_mod, "_move", broken)
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert [t.path for t in s.queue] == [b, a, c]  # b keeps playing, a is next
    assert a.exists() and s.history == [] and sum(s.counts.values()) == 0
    [n] = s.notices_since(0)
    assert n["level"] == "error" and "disk is read-only" in n["message"]
    assert journal_lines(tmp_path)[0]["event"] == "failed"


def test_failed_copy_removes_partial_file(music, make_session, monkeypatch, settings):
    settings["file_operation"] = "copy"

    def half_copy(src, dest):
        dest.write_bytes(b"partial")
        raise OSError("device full")

    (a,) = tracks(music, "a.mp3")
    s = make_session([a])
    monkeypatch.setattr(session_mod, "_copy", half_copy)
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert not (keep_dir(music) / "a.mp3").exists()
    assert s.current.path == a


def test_failed_undo_keeps_action_in_history(music, make_session, monkeypatch):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    s.sort(s.current.id, "swipe", 0)
    s.flush()

    def broken(src, dest):
        raise OSError("nope")

    monkeypatch.setattr(session_mod, "_move", broken)
    s.undo_last()
    s.flush()
    assert len(s.history) == 1 and s.current.path == b
    assert (keep_dir(music) / "a.mp3").exists()
    assert s.notices_since(0)[-1]["level"] == "error"


# ------------------------------------------------------------------ copy mode


def test_copy_mode_keeps_source_and_indexes(music, make_session, settings):
    settings["file_operation"] = "copy"
    settings["cleanup_empty_source_folders_after_sort"] = True  # ignored in copy mode
    a, b = tracks(music, "Sub/a.mp3", "b.mp3")
    s = make_session([a, b])
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert a.exists() and (keep_dir(music) / "a.mp3").exists()
    idx = load_copied_index(music)
    assert idx["Sub/a.mp3"]["dest"] == "BAKKIS_SORTED/DJ Sorting/Keep/a.mp3"
    assert filter_already_copied([a, b], music) == [b]
    # A replaced source (different size) shows up again.
    a.write_bytes(b"a different, longer file")
    assert filter_already_copied([a, b], music) == [a, b]


def test_copy_mode_undo_deletes_only_the_copy(music, make_session, settings):
    settings["file_operation"] = "copy"
    (a,) = tracks(music, "a.mp3")
    s = make_session([a])
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    s.undo_last()
    s.flush()
    assert a.read_bytes() == b"a.mp3"
    assert not (keep_dir(music) / "a.mp3").exists()
    assert load_copied_index(music) == {}


# ------------------------------------------------------------------ queue


def test_set_order_and_play_next(music, make_session):
    paths = tracks(music, "c.mp3", "a.mp3", "d.mp3", "b.mp3")
    s = make_session(list(paths))
    s.set_order("in_order")
    assert [t.path.name for t in s.queue] == ["c.mp3", "d.mp3", "a.mp3", "b.mp3"]
    last = s.queue[-1]
    s.play_next(last.id)
    assert s.queue[1] is last
    with pytest.raises(SessionError):
        s.play_next("missing")


def test_track_path_lookup_follows_moves(music, make_session):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    tid = s.current.id
    s.sort(tid, "swipe", 0)
    s.flush()
    assert s.track_path(tid) == keep_dir(music) / "a.mp3"
    assert s.track_path("nope") is None


def test_folder_count(music, make_session):
    touch(keep_dir(music) / "x.mp3")
    touch(keep_dir(music) / "notes.txt")
    (a,) = tracks(music, "a.mp3")
    s = make_session([a])
    preset = s.presets.active("swipe")
    assert s.folder_count(preset, 0) == 1
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert s.folder_count(preset, 0) == 2
    assert s.folder_count(preset, 1) == 0


def test_on_change_called(music, make_session):
    calls = []
    (a,) = tracks(music, "a.mp3")
    s = make_session([a], on_change=lambda: calls.append(1))
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    assert calls


def test_count_info_survives_preset_delete(music, make_session):
    a, b = tracks(music, "a.mp3", "b.mp3")
    s = make_session([a, b])
    extra = s.presets.create("swipe", "Temp", ["Yes", "No", "Wow"], [])
    s.presets.set_active(extra["id"])
    s.sort(s.current.id, "swipe", 1)
    s.flush()
    s.presets.delete(extra["id"])
    info = s.snapshot()["count_info"][f"{extra['id']}:1"]
    assert (info["label"], info["preset"], info["slot"]) == ("No", "Temp", 1)


def test_queue_search(music, make_session):
    paths = tracks(music, "Promos/Maceo Plex - Sheets.mp3", "Other/Solomun - Home.mp3", "Promos/Solomun - Kackvogel.mp3", "a.mp3")
    s = make_session(paths)
    res = s.queue_slice(q="solomun")
    assert [t["name"] for t in res["items"]] == ["Solomun - Home.mp3", "Solomun - Kackvogel.mp3"]
    assert [t["index"] for t in res["items"]] == [0, 1]
    assert s.queue_slice(q="promos solomun")["total"] == 1
    assert s.queue_slice(q="  ")["total"] == 3
