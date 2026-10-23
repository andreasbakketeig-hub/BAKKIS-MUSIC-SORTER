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


def test_like_appends_once(music):
    tree(music)
    a = music.resolve() / "BAKKIS_SORTED" / "Quick Sort" / "Keep" / "a.mp3"
    r1 = listen.like(music, a, metadata.read_meta)
    r2 = listen.like(music, a, metadata.read_meta)
    assert r1["added"] and not r2["added"] and r2["count"] == 1
    lines = open(r1["path"]).read().splitlines()
    assert lines[0] == "#EXTM3U" and lines[-1] == str(a)


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
    assert api(server, "listen_like", track_id=tid)[1]["result"]["added"]
    assert api(server, "listen_start", sources=["sorted:../nope"])[0] == 400
    new = {p for p in music.rglob("*") if p.is_file()} - set(before)
    assert {p.name for p in new} == {listen.LIKED_PLAYLIST}  # nothing moved; only the liked playlist is new
    assert all(p.exists() for p in before)
    assert api(server, "listen_stop")[1]["result"] is True
    assert api(server, "track_meta", track_id=tid)[0] == 404
