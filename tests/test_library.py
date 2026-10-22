import random
import threading
from datetime import date

from bakkis_sorter import library
from bakkis_sorter.config import PresetStore

from .conftest import touch

SWIPE = {"id": "s", "name": "DJ Sorting", "mode": "swipe", "labels": ["Keep", "Archive", "Favorite"],
         "colors": [], "folders": ["KEEP", "ARCHIVE", "FAVORITES"]}
GRID = {"id": "g", "name": "Genre/Sorting", "mode": "grid", "labels": ["House"] * 9, "colors": [],
        "folders": ["House", "Tech: House"] + ["Other"] * 7}
DAY = date(2026, 10, 23)


def names(paths, root):
    return sorted(library.relative_posix(p, root) for p in paths)


def test_scan_formats_and_junk(music):
    for n in ["a.mp3", "b.WAV", "c.aiff", "d.aif", "e.flac", "f.m4a", "g.aac", "h.alac", "i.ogg",
              "notes.txt", "cover.jpg", ".hidden.mp3", "._a.mp3", ".DS_Store"]:
        touch(music / n)
    found = names(library.scan(music), music)
    assert found == ["a.mp3", "b.WAV", "c.aiff", "d.aif", "e.flac", "f.m4a", "g.aac", "h.alac", "i.ogg"]


def test_scan_skips_sorted_hidden_legacy_and_preset_dirs(music):
    touch(music / "top.mp3")
    touch(music / "Promos" / "Label" / "deep.mp3")
    touch(music / "BAKKIS_SORTED" / "DJ Sorting" / "Keep" / "sorted.mp3")
    touch(music / ".Trash" / "t.mp3")
    touch(music / "ARCHIVE" / "old.mp3")
    touch(music / "2025-01-01 - Techno" / "dated.mp3")
    touch(music / "Techno" / "genre.mp3")
    skip = library.build_skip_dir_names(PresetStore().skip_folder_names())
    found = names(library.scan(music, skip_dir_names=skip), music)
    assert found == ["Promos/Label/deep.mp3", "top.mp3"]


def test_scan_non_recursive(music):
    touch(music / "b.mp3")
    touch(music / "a.mp3")
    touch(music / "sub" / "c.mp3")
    found = library.scan(music, recursive=False)
    assert [p.name for p in found] == ["a.mp3", "b.mp3"]


def test_scan_cancel(music):
    for i in range(100):
        touch(music / f"{i}.mp3")
    ev = threading.Event()
    seen = []

    def progress(n):
        seen.append(n)
        ev.set()

    assert library.scan(music, cancel=ev, on_progress=progress) is None
    assert seen == [library.PROGRESS_EVERY]


def test_scan_job_done_and_cancel(music):
    for i in range(50):
        touch(music / f"{i}.mp3")
    done = []
    job = library.ScanJob(music, on_done=done.append).start()
    job.join(5)
    assert len(done) == 1 and len(done[0]) == 50
    assert job.result == done[0]

    done2 = []
    job2 = library.ScanJob(music, on_done=done2.append)
    job2.cancel()
    job2.start()
    job2.join(5)
    assert done2 == [] and job2.result is None


def test_scan_job_reports_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(library, "scan", boom)
    errors, done = [], []
    job = library.ScanJob(tmp_path, on_error=errors.append, on_done=done.append).start()
    job.join(5)
    assert [str(e) for e in errors] == ["disk gone"] and done == []


def test_order_tracks(music):
    paths = [music / n for n in ["b.mp3", "A.mp3", "c.mp3"]]
    assert [p.name for p in library.order_tracks(paths, "in_order")] == ["A.mp3", "b.mp3", "c.mp3"]
    shuffled = library.order_tracks(paths, "random", random.Random(1))
    assert sorted(shuffled) == sorted(paths)


def test_reorder_keep_current(music):
    q = [music / n for n in ["c.mp3", "a.mp3", "d.mp3", "b.mp3"]]
    assert [p.name for p in library.reorder_keep_current(q, "in_order")] == ["c.mp3", "d.mp3", "a.mp3", "b.mp3"]
    r = library.reorder_keep_current(q, "random", random.Random(3))
    assert r[0] == q[0] and sorted(r) == sorted(q)
    assert library.reorder_keep_current([], "random") == []


def test_source_folder_display(music):
    assert library.source_folder_display(music / "Promos" / "Label" / "x.mp3", music) == "Promos / Label"
    assert library.source_folder_display(music / "x.mp3", music) == ""


def test_destinations(music):
    r = music.resolve()
    assert library.slot_destination_dir(music, SWIPE, 0, dated=False) == r / "BAKKIS_SORTED" / "DJ Sorting" / "Keep"
    assert library.slot_destination_dir(music, SWIPE, 2, dated=True, today=DAY) == (
        r / "BAKKIS_SORTED" / "DJ Sorting" / "2026-10-23" / "Favorite"
    )
    assert library.slot_destination_dir(music, GRID, 1, dated=False) == r / "BAKKIS_SORTED" / "Genre-Sorting" / "Tech- House"


def test_skipped_destination_preserves_subfolders(music):
    r = music.resolve()
    src = r / "Promos" / "Label" / "x.mp3"
    assert library.skipped_destination_dir(music, SWIPE, src, dated=True, today=DAY) == (
        r / "BAKKIS_SORTED" / "DJ Sorting" / "2026-10-23" / "Skipped" / "Promos" / "Label"
    )
    assert library.skipped_destination_dir(music, SWIPE, r / "x.mp3", dated=False) == (
        r / "BAKKIS_SORTED" / "DJ Sorting" / "Skipped"
    )


def test_unique_destination(tmp_path):
    assert library.unique_destination(tmp_path, "a.mp3") == tmp_path / "a.mp3"
    touch(tmp_path / "a.mp3")
    touch(tmp_path / "a (2).mp3")
    assert library.unique_destination(tmp_path, "a.mp3") == tmp_path / "a (3).mp3"


def test_cleanup_empty_parents(music):
    r = music.resolve()
    keep = touch(r / "Promos" / "keep.mp3")
    src = touch(r / "Promos" / "Label" / "EP" / "x.mp3")
    touch(r / "Promos" / "Label" / "EP" / ".DS_Store")
    touch(r / "Promos" / "Label" / "._x.mp3")
    src.unlink()  # simulate the move
    deleted, err = library.cleanup_empty_parents(src, music)
    assert err is None
    assert deleted == [r / "Promos" / "Label" / "EP", r / "Promos" / "Label"]
    assert keep.exists() and (r / "Promos").is_dir()


def test_cleanup_never_removes_root_or_sorted_tree(music):
    r = music.resolve()
    src = touch(r / "x.mp3")
    src.unlink()
    assert library.cleanup_empty_parents(src, music) == ([], None)
    assert r.is_dir()
    inside = touch(r / "BAKKIS_SORTED" / "P" / "Keep" / "y.mp3")
    inside.unlink()
    assert library.cleanup_empty_parents(inside, music) == ([], None)
    assert (r / "BAKKIS_SORTED" / "P" / "Keep").is_dir()


def test_cleanup_stops_at_nonempty_subdir(music):
    r = music.resolve()
    src = touch(r / "A" / "x.mp3")
    (r / "A" / "emptychild").mkdir()
    src.unlink()
    assert library.cleanup_empty_parents(src, music) == ([], None)
    assert (r / "A").is_dir()
