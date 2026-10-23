from datetime import date

from mutagen.id3 import ID3, TIT2, TPE1

from bakkis_sorter import feedback, history, metadata
from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session

from .audio_fixtures import make_mp3


def tagged(path, title, artist):
    make_mp3(path)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TPE1(encoding=3, text=artist))
    t.save(str(path))
    return path


def test_feedback_text_for_a_promos_session(music, tmp_path):
    presets = PresetStore()
    promos = next(p for p in presets.presets if p["name"] == "Promos")
    presets.set_active(promos["id"])
    paths = [tagged(music / f"{i}.mp3", f"Track {i}", "Artist") for i in range(6)]
    s_ = default_settings()
    s_["create_dated_sorting_sessions"] = False
    journal = Journal(tmp_path / "journal")
    s = Session(music, paths, presets=presets, settings=lambda: s_, journal=journal, today=lambda: date(2026, 10, 23))
    s.sort(s.current.id, "swipe", 1)  # Not for me
    s.sort(s.current.id, "swipe", 2)  # Feeling it
    s.sort(s.current.id, "swipe", 0)  # Maybe
    s.skip(s.current.id, "swipe")  # not a verdict
    s.sort(s.current.id, "swipe", 2)  # Feeling it, then undone
    s.flush()
    s.undo_last()
    s.sort(s.current.id, "swipe", 2)  # Feeling it again (the same track)
    s.close()
    (sess,) = history.load(journal)
    res = feedback.build(sess["items"], metadata.read_meta, presets.presets)
    lines = res["text"].splitlines()
    assert res["count"] == 4
    assert lines[0].startswith("Promos – ")
    assert lines[2:] == [
        "Feeling it (2)", "• Artist – Track 1", "• Artist – Track 4", "",
        "Maybe (1)", "• Artist – Track 2", "",
        "Not for me (1)", "• Artist – Track 0",
    ]


def test_old_entries_without_slot_and_two_presets():
    presets = [{"name": "Demos", "labels": ["Hold", "Reject", "Sign"]}]
    items = [
        {"preset": "Demos", "label": "Reject", "mode": "swipe", "src": "/x/Producer - Tune A.mp3", "ts": "2026-10-23T10:00:00"},
        {"preset": "Demos", "label": "Sign", "mode": "swipe", "src": "/x/Producer - Tune B.mp3", "ts": "2026-10-23T10:01:00"},
        {"preset": "Genre Sort", "label": "Techno", "mode": "grid", "slot": 6, "src": "/x/C.mp3", "ts": "2026-10-23T10:02:00"},
        {"preset": "Genre Sort", "label": "House", "mode": "grid", "slot": 0, "src": "/x/D.mp3", "ts": "2026-10-23T10:03:00"},
    ]
    text = feedback.build(items, metadata.read_meta, presets)["text"]
    demos, genres = text.split("\n\n\n")
    assert demos.splitlines()[2] == "Sign (1)" and "Reject (1)" in demos
    assert genres.splitlines()[0] == "Genre Sort – 23 Oct 2026"
    assert genres.index("House") < genres.index("Techno")
    assert feedback.build([], metadata.read_meta, presets) == {"text": "", "count": 0}
