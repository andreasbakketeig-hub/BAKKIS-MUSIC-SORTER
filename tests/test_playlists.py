from mutagen.id3 import ID3, TIT2, TPE1

from bakkis_sorter import metadata, playlists

from .audio_fixtures import make_mp3
from .conftest import touch


def test_export_one_playlist_per_folder(music):
    s = music.resolve() / "BAKKIS_SORTED"
    a = make_mp3(s / "DJ Sorting" / "Keep" / "a.mp3")
    t = ID3()
    t.add(TIT2(encoding=3, text="Sleeper"))
    t.add(TPE1(encoding=3, text="Jimpster"))
    t.save(str(a))
    make_mp3(s / "DJ Sorting" / "Keep" / "b.mp3")
    make_mp3(s / "DJ Sorting" / "2026-10-23" / "Favorite" / "c.mp3")
    make_mp3(s / "DJ Sorting" / "Skipped" / "Deep" / "x.mp3")
    touch(s / "DJ Sorting" / "Archive" / "notes.txt")  # no audio -> no playlist
    res = playlists.export(music, metadata.read_meta)
    names = sorted(p["name"] for p in res["playlists"])
    assert names == ["DJ Sorting - 2026-10-23 - Favorite", "DJ Sorting - Keep"]
    keep = (s / "_Playlists" / "DJ Sorting - Keep.m3u8").read_text().splitlines()
    assert keep[0] == "#EXTM3U"
    assert keep[2].startswith("#EXTINF:1,Jimpster - Sleeper") and keep[3] == str(a)
    assert keep[-1].endswith("b.mp3")
    # Re-export overwrites our own playlists and never lists the playlist folder itself
    again = playlists.export(music, metadata.read_meta)
    assert len(again["playlists"]) == 2


def test_export_with_nothing_sorted(music):
    assert playlists.export(music, metadata.read_meta)["playlists"] == []


def test_export_covers_copies_and_second_pass_folders(music):
    r = music.resolve()
    make_mp3(r / "BAKKIS_SORTED" / "Genre Sort" / "Techno" / "Quick Sort" / "Keep" / "a.mp3")
    make_mp3(r / "BAKKIS_SORTED_COPIES" / "Quick Sort" / "Keep" / "b.mp3")
    res = playlists.export(music / "BAKKIS_SORTED" / "Genre Sort" / "Techno", metadata.read_meta)  # from a second pass
    assert sorted(p["name"] for p in res["playlists"]) == ["Genre Sort - Techno - Quick Sort - Keep", "Quick Sort - Keep (copies)"]
    assert (r / "BAKKIS_SORTED" / "_Playlists" / "Genre Sort - Techno - Quick Sort - Keep.m3u8").exists()
    assert (r / "BAKKIS_SORTED_COPIES" / "_Playlists" / "Quick Sort - Keep (copies).m3u8").exists()
    assert res["folder"] == str(r / "BAKKIS_SORTED" / "_Playlists")
