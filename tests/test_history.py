from datetime import date

import pytest

from bakkis_sorter import history
from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session, load_copied_index

from .conftest import touch


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "journal")


def run_session(music, paths, journal, **settings_over):
    s_ = default_settings()
    s_.update(create_dated_sorting_sessions=False, **settings_over)
    s = Session(music, paths, presets=PresetStore(), settings=lambda: s_, journal=journal, today=lambda: date(2026, 10, 23))
    return s


def tracks(music, *names):
    return [touch(music.resolve() / n, n.encode()) for n in names]


def items(sessions):
    return [i for s in sessions for i in s["items"]]


def test_history_lists_sorts_still_in_effect(music, journal):
    a, b, c = tracks(music, "a.mp3", "b.mp3", "Sub/c.mp3")
    s = run_session(music, [a, b, c], journal)
    s.sort(s.current.id, "swipe", 0)
    s.sort(s.current.id, "swipe", 1)
    s.skip(s.current.id, "swipe")
    s.flush()
    s.undo_last()  # c is back: must not show up
    s.close()
    sessions = history.load(journal)
    assert len(sessions) == 1 and sessions[0]["root"] == str(music.resolve())
    got = sorted((i["name"], i["label"], i["status"]) for i in items(sessions))
    assert got == [("a.mp3", "Keep", "sorted"), ("b.mp3", "Archive", "sorted")]


def test_restore_puts_files_back_and_never_overwrites(music, journal):
    a, b = tracks(music, "a.mp3", "Deep/b.mp3")
    s = run_session(music, [a, b], journal, cleanup_empty_source_folders_after_sort=True)
    s.sort(s.current.id, "swipe", 0)
    s.sort(s.current.id, "swipe", 0)
    s.close()
    assert not (music / "Deep").exists()  # cleaned up
    touch(a, b"a new file with the same name")
    ids = {i["name"]: i["id"] for i in items(history.load(journal))}
    res = history.restore(journal, [ids["a.mp3"], ids["b.mp3"]])
    assert all(r["ok"] for r in res)
    assert a.read_bytes() == b"a new file with the same name"
    assert (music.resolve() / "a (2).mp3").read_bytes() == b"a.mp3"
    assert "the name was taken" in res[0]["message"]
    assert b.read_bytes() == b"Deep/b.mp3"  # folder recreated
    assert history.load(journal) == []  # nothing left in effect
    again = history.restore(journal, [ids["b.mp3"]])
    assert not again[0]["ok"]


def test_restore_copy_deletes_only_the_copy(music, journal):
    (a,) = tracks(music, "a.mp3")
    s = run_session(music, [a], journal, file_operation="copy")
    s.sort(s.current.id, "swipe", 0)
    s.close()
    (item,) = items(history.load(journal))
    assert history.restore(journal, [item["id"]])[0]["ok"]
    assert a.exists() and not (music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep" / "a.mp3").exists()
    assert load_copied_index(music) == {}


def test_current_session_items_are_protected(music, journal):
    (a,) = tracks(music, "a.mp3")
    s = run_session(music, [a], journal)
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    active = s.active_dests()
    (item,) = items(history.load(journal, active_dests=active))
    assert item["status"] == "current"
    res = history.restore(journal, [item["id"]], active_dests=active)
    assert not res[0]["ok"] and "Undo" in res[0]["message"]
    s.close()


def test_missing_destination(music, journal):
    (a,) = tracks(music, "a.mp3")
    s = run_session(music, [a], journal)
    s.sort(s.current.id, "swipe", 0)
    s.close()
    (item,) = items(history.load(journal))
    __import__("os").remove(item["dest"])  # user moved it in Finder
    assert items(history.load(journal))[0]["status"] == "missing"
    assert not history.restore(journal, [item["id"]])[0]["ok"]
    assert not history.restore(journal, ["nope:1"])[0]["ok"]
