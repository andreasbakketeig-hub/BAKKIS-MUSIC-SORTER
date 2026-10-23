from mutagen.id3 import ID3, TIT2, TPE1

from bakkis_sorter import metadata
from bakkis_sorter.duplicates import DuplicateIndex, norm

from .audio_fixtures import make_mp3


def tagged(path, title, artist, frames=40):
    make_mp3(path, frames)
    t = ID3()
    t.add(TIT2(encoding=3, text=title))
    t.add(TPE1(encoding=3, text=artist))
    t.save(str(path))
    return path


def test_norm():
    assert norm("Home (Original Mix)") == "home"
    assert norm("Home!  (Club Mix)") == "home club mix"
    assert norm(None) == ""


def test_finds_sorted_copy(music):
    keep = music / "BAKKIS_SORTED" / "DJ Sorting" / "2026-10-01" / "Keep"
    keep.mkdir(parents=True)
    sorted_copy = tagged(keep / "x.mp3", "Home (Original Mix)", "Solomun")
    new = tagged(music / "Promos" / "y.mp3", "Home", "SOLOMUN")
    other = tagged(music / "z.mp3", "Home", "Someone Else")
    longer = tagged(music / "w.mp3", "Home", "Solomun", frames=400)  # ~10 s longer: different edit
    idx = DuplicateIndex(music, metadata.read_meta).start()
    assert idx.ready.wait(5)
    hit = idx.find(new, metadata.read_meta(new))
    assert hit == {"path": str(sorted_copy.resolve()), "folder": "Keep · DJ Sorting"}
    assert idx.find(other, metadata.read_meta(other)) is None
    assert idx.find(longer, metadata.read_meta(longer)) is None
    assert idx.find(sorted_copy.resolve(), metadata.read_meta(sorted_copy)) is None  # itself


def test_add_after_sort_and_missing_file(music):
    idx = DuplicateIndex(music, metadata.read_meta).start()
    assert idx.ready.wait(5)
    a = tagged(music / "a.mp3", "Sleeper", "Jimpster")
    dest = music / "BAKKIS_SORTED" / "P" / "Keep" / "a.mp3"
    dest.parent.mkdir(parents=True)
    b = tagged(dest, "Sleeper", "Jimpster")
    idx.add(b)
    assert idx.find(a, metadata.read_meta(a))["folder"] == "Keep · P"
    b.unlink()
    assert idx.find(a, metadata.read_meta(a)) is None


def test_untagged_files_are_not_matched(music):
    keep = music / "BAKKIS_SORTED" / "P" / "Keep"
    keep.mkdir(parents=True)
    make_mp3(keep / "Artist - Song.mp3")
    idx = DuplicateIndex(music, metadata.read_meta).start()
    assert idx.ready.wait(5)
    new = make_mp3(music / "Artist - Song.mp3")
    assert idx.find(new, metadata.read_meta(new)) is None
