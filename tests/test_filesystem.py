"""End-to-end file system checks: nothing lost, nothing duplicated, no leftover folders.

Every test works in a temp folder. A "snapshot" is every directory and every file with a hash of
its content, so "identical tree" really means identical: same folders, same files, same bytes.
"""

import hashlib
import os
import random
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from bakkis_sorter import history, library
from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session

def is_junk(name: str) -> bool:
    """Files macOS creates on its own; the cleanup removes them on purpose."""
    return name == ".DS_Store" or name.startswith("._")


def snapshot(root: Path) -> dict:
    """{relative path: content hash or 'dir'} for everything under root, macOS junk files excluded."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        for d in dirs:
            out[str(rel_dir / d)] = "dir"
        for f in files:
            if is_junk(f):
                continue
            p = Path(dirpath, f)
            out[str(rel_dir / f)] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def contents(root: Path, skip_sorted: bool = False) -> list:
    """Sorted list of the content hashes of all audio files (where they are doesn't matter)."""
    out = []
    for p in root.rglob("*"):
        if p.is_file() and not is_junk(p.name) and library.is_audio_path(p) and not (skip_sorted and {"BAKKIS_SORTED", "BAKKIS_SORTED_COPIES"} & set(p.parts)):
            out.append(hashlib.sha1(p.read_bytes()).hexdigest())
    return sorted(out)


def make_tree(root: Path, n: int = 30, seed: int = 1) -> list:
    """Nested folders with unique audio files plus the junk macOS leaves around."""
    rng = random.Random(seed)
    folders = ["", "Promos", "Promos/Label A", "Promos/Label A/EP 1", "Deep Tech", "Ünicode – Folder", "a/b/c/d"]
    paths = []
    for i in range(n):
        folder = rng.choice(folders)
        name = rng.choice(["Track", "Ålesund – Sång", "Artist - Title (Original Mix)", "x"]) + f" {i}" + rng.choice([".mp3", ".wav", ".aiff", ".flac"])
        p = root / folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"unique audio {i} {rng.random()}".encode())
        paths.append(p)
    for junk, data in (("Promos/.DS_Store", b"junk"), ("Promos/Label A/._Track 1.mp3", b"appledouble")):
        (root / junk).parent.mkdir(parents=True, exist_ok=True)
        (root / junk).write_bytes(data)
    (root / "Deep Tech").mkdir(exist_ok=True)
    (root / "Deep Tech" / "notes.txt").write_text("keep me")
    return paths


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    settings = default_settings()
    settings.update(create_dated_sorting_sessions=True, cleanup_empty_source_folders_after_sort=True)
    journal = Journal(tmp_path / "journal")

    def session(paths=None):
        paths = paths if paths is not None else library.order_tracks(library.scan(root), "in_order")
        presets = PresetStore()
        for p in presets.presets:
            presets.update(p["id"], dated=settings["create_dated_sorting_sessions"])
        return Session(root, paths, presets=presets, settings=lambda: settings, journal=journal, today=lambda: date(2026, 10, 23))

    return root, settings, journal, session


# ------------------------------------------------------------------ the basics


def test_sorting_creates_only_the_folders_it_needs(setup):
    root, settings, journal, session = setup
    make_tree(root, 6)
    s = session()
    s.sort(s.current.id, "swipe", 0)
    s.flush()
    sorted_dirs = sorted(str(p.relative_to(root)) for p in (root / "BAKKIS_SORTED").rglob("*") if p.is_dir())
    assert sorted_dirs == ["BAKKIS_SORTED/Quick Sort", "BAKKIS_SORTED/Quick Sort/2026-10-23", "BAKKIS_SORTED/Quick Sort/2026-10-23/Keep"]
    s.close()


def test_move_session_then_undo_all_restores_the_exact_tree(setup):
    root, settings, journal, session = setup
    make_tree(root)
    before = snapshot(root)
    originals = contents(root)
    s = session()
    rng = random.Random(7)
    while s.current:
        if rng.random() < 0.2:
            s.skip(s.current.id, rng.choice(["swipe", "grid"]))
        else:
            mode = rng.choice(["swipe", "grid"])
            s.sort(s.current.id, mode, rng.randrange(3 if mode == "swipe" else 9))
    s.flush()
    assert contents(root) == originals  # every file exactly once, bytes intact
    assert not contents(root, skip_sorted=True)  # everything left the source folders
    assert not (root / "a").exists()  # empty source folders were cleaned up
    assert (root / "Deep Tech" / "notes.txt").exists()  # folders with other files stay
    s.undo_all()
    s.flush()
    s.close()
    assert snapshot(root) == before  # same folders, same files, same bytes, nothing left behind


def test_copy_session_then_undo_all_restores_the_exact_tree(setup):
    root, settings, journal, session = setup
    settings["file_operation"] = "copy"
    make_tree(root)
    before = snapshot(root)
    s = session()
    while s.current:
        s.sort(s.current.id, "swipe", random.randrange(3))
    s.flush()
    assert not (root / "BAKKIS_SORTED").exists()
    assert contents(root, skip_sorted=True) == contents(root / "BAKKIS_SORTED_COPIES") == sorted(
        h for k, h in before.items() if h != "dir" and library.is_audio_path(Path(k))  # junk is not in snapshots
    )
    s.undo_all()
    s.flush()
    s.close()
    assert snapshot(root) == before


def test_history_put_back_leaves_no_empty_folders(setup):
    root, settings, journal, session = setup
    make_tree(root, 10)
    before = snapshot(root)
    s = session()
    while s.current:
        s.sort(s.current.id, "swipe", 1)
    s.close()
    ids = [i["id"] for sess in history.load(journal) for i in sess["items"]]
    assert all(r["ok"] for r in history.restore(journal, ids))
    assert snapshot(root) == before


# ------------------------------------------------------------------ long random sequences


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("op", ["move", "copy"])
def test_random_sequences_never_lose_or_duplicate_files(setup, seed, op):
    root, settings, journal, session = setup
    settings["file_operation"] = op
    settings["cleanup_empty_source_folders_after_sort"] = seed % 2 == 0
    settings["create_dated_sorting_sessions"] = seed % 3 != 0
    make_tree(root, 25, seed)
    before = snapshot(root)
    originals = contents(root)
    s = session()
    rng = random.Random(seed)
    for _ in range(150):
        r = rng.random()
        if r < 0.55 and s.current:
            mode = rng.choice(["swipe", "grid"])
            s.sort(s.current.id, mode, rng.randrange(3 if mode == "swipe" else 9))
        elif r < 0.7 and s.current:
            s.skip(s.current.id, "swipe")
        elif r < 0.95:
            s.undo_last()
        else:
            s.undo_all()
        s.flush()
        now = contents(root)
        if op == "move":
            assert now == originals  # each file exists exactly once
        else:
            assert contents(root, skip_sorted=True) == originals  # originals never touched
            assert set(now) == set(originals)  # copies are only ever of real tracks
        with s.lock:
            for t in s.queue:  # every queued track points at a real file
                assert t.path.exists(), t.path
    s.undo_all()
    s.flush()
    s.close()
    assert snapshot(root) == before


# ------------------------------------------------------------------ real failures


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_read_only_source_folder_fails_cleanly(setup):
    root, settings, journal, session = setup
    locked = root / "Locked"
    locked.mkdir()
    track = locked / "a.mp3"
    track.write_bytes(b"locked track")
    os.chmod(locked, 0o555)  # cannot remove files from here
    try:
        s = session([track])
        s.sort(s.current.id, "swipe", 0)
        s.flush()
        assert track.read_bytes() == b"locked track"
        assert not list((root / "BAKKIS_SORTED").rglob("a.mp3")) if (root / "BAKKIS_SORTED").exists() else True
        assert s.current is not None and s.current.path == track  # back in the queue
        assert any(n["level"] == "error" for n in s.notices_since(0))
        s.close()
    finally:
        os.chmod(locked, 0o755)


# ------------------------------------------------------------------ USB stick (exFAT disk image)


@pytest.mark.skipif(shutil.which("hdiutil") is None, reason="macOS only")
def test_exfat_volume_like_a_usb_stick(tmp_path):
    image = tmp_path / "stick.dmg"
    subprocess.run(["hdiutil", "create", "-size", "40m", "-fs", "ExFAT", "-volname", "BAKKISTEST", str(image)], check=True, capture_output=True)
    mount = tmp_path / "mnt"
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", str(image), "-mountpoint", str(mount), "-nobrowse", "-quiet"], check=True)
    try:
        root = mount / "Music"
        root.mkdir()
        make_tree(root, 15)
        before_audio = contents(root)
        settings = default_settings()
        settings.update(cleanup_empty_source_folders_after_sort=True)
        journal = Journal(tmp_path / "journal")
        s = Session(root, library.order_tracks(library.scan(root), "in_order"), presets=PresetStore(), settings=lambda: settings,
                    journal=journal, today=lambda: date(2026, 10, 23))
        assert len(s.queue) == 15  # macOS ._ files on exFAT are never scanned
        while s.current:
            s.sort(s.current.id, "swipe", random.randrange(3))
        s.flush()
        assert contents(root) == before_audio
        s.undo_all()
        s.flush()
        s.close()
        assert contents(root, skip_sorted=True) == before_audio
        leftovers = [p for p in (root / "BAKKIS_SORTED").rglob("*") if p.is_file() and not p.name.startswith("._")] if (root / "BAKKIS_SORTED").exists() else []
        assert leftovers == []
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force", "-quiet"], check=False)
