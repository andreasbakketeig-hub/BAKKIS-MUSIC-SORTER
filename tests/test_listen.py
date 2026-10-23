import random

from bakkis_sorter import listen, metadata

from .audio_fixtures import make_mp3
from .test_server import api, app, load, request  # noqa: F401  (fixture + helpers)


def tree(music):
    s = music.resolve() / "BAKKIS_SORTED"
    for p in ["Quick Sort/Keep/a.mp3", "Quick Sort/Keep/b.mp3", "Quick Sort/2026-10-23/Favorite/c.mp3",
              "Quick Sort/Skipped/Deep/x.mp3", "_Playlists/x.mp3"]:
        make_mp3(s / p)
    make_mp3(music / "new1.mp3")
    make_mp3(music / "new2.mp3")


def test_sorted_sources(music):
    tree(music)
    src = listen.sorted_sources(music)
    assert [(x["label"], x["count"]) for x in src] == [("Favorite · Quick Sort · 2026-10-23", 1), ("Keep · Quick Sort", 2)]
    assert listen.resolve_source(music, "sorted:../../etc") is None
    assert listen.resolve_source(music, "queue") is None
    assert listen.resolve_source(music, src[1]["key"]).name == "Keep"


def test_session_dedupes_and_shuffles(tmp_path):
    paths = [tmp_path / f"{i}.mp3" for i in range(20)]
    ls = listen.ListenSession([(p, "A") for p in paths] + [(paths[0], "B")], rng=random.Random(1))
    assert len(ls.tracks) == 20 and [t.path for t in ls.tracks] != paths
    assert ls.track_path(ls.tracks[3].id) == ls.tracks[3].path and ls.track_path("nope") is None


def test_hearts_toggle_persist_and_create_folder(music, tmp_path):
    from bakkis_sorter.session import Journal

    tree(music)
    keep = music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep"
    a, b = keep / "a.mp3", keep / "b.mp3"
    hearts = listen.Hearts(tmp_path / "hearts.json")
    hearts.set(a, True, "Keep")
    hearts.set(b, True, "Keep")
    hearts.set(b, False)  # toggled off again
    hearts.set(a, True)  # no duplicates
    again = listen.Hearts(tmp_path / "hearts.json")  # survives a restart
    assert [i["name"] for i in again.items] == ["a.mp3"]
    journal = Journal(tmp_path / "journal")
    r1 = listen.create_folder(music, again, journal, "Hearted")
    r2 = listen.create_folder(music, again, journal, "Hearted")
    assert r1["copied"] == 1 and r1["folder"].endswith("BAKKIS_SORTED/Discover/Hearted")
    assert r2["folder"].endswith("Hearted (2)")  # never overwrites
    assert a.exists()  # copies only


def test_current_location_follows_sorts(music, tmp_path):
    from bakkis_sorter.session import Journal

    journal = Journal(tmp_path / "journal")
    src = music.resolve() / "x.mp3"
    dest = music.resolve() / "BAKKIS_SORTED" / "Q" / "Keep" / "x.mp3"
    make_mp3(dest)
    journal.append({"event": "sort", "op": "move", "src": str(src), "dest": str(dest)})
    assert listen.current_location(src, journal) == dest
    journal.append({"event": "undo", "op": "move", "src": str(src), "dest": str(dest)})
    assert listen.current_location(src, journal) is None  # undone: it should be back at src, but isn't


def test_listen_api_never_moves_files(app, music):  # noqa: F811
    backend, server = app
    tree(music)
    before = sorted(p for p in music.rglob("*") if p.is_file())
    load(server, music)
    sources = api(server, "listen_sources")[1]["result"]
    keys = [x["key"] for x in sources]
    assert keys[0] == "queue" and sources[0]["count"] == 2
    res = api(server, "listen_start", sources=keys)[1]["result"]
    assert res["total"] == 5  # 2 unsorted + 2 Keep + 1 Favorite; Skipped and _Playlists left out
    tid = res["tracks"][0]["id"]
    r, data = request(server, "GET", f"/audio/{tid}?t={server.token}")
    assert r.status == 200 and data
    assert api(server, "track_meta", track_id=tid)[0] == 200
    assert api(server, "listen_heart", track_id=tid, on=True)[1]["result"] == {"hearted": True, "count": 1}
    assert api(server, "listen_heart", track_id=tid, on=False)[1]["result"] == {"hearted": False, "count": 0}
    api(server, "listen_heart", track_id=tid, on=True)
    assert [h["missing"] for h in api(server, "hearts")[1]["result"]] == [False]
    assert api(server, "listen_start", sources=["sorted:../nope"])[0] == 400
    assert {p for p in music.rglob("*") if p.is_file()} == set(before)  # listening + hearting touches no files
    made = api(server, "hearts_create_folder")[1]["result"]
    assert made["copied"] == 1 and api(server, "hearts")[1]["result"] == []  # list emptied after the folder
    assert all(p.exists() for p in before)
    assert api(server, "listen_stop")[1]["result"] is True
    assert api(server, "track_meta", track_id=tid)[0] == 404
