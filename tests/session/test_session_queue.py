"""Targeted unit tests for Session queue, fast cached search, and filtering."""

from datetime import date
from pathlib import Path
import pytest

from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.session import Journal, Session, Track
from bakkis_sorter.session.models import new_track


@pytest.fixture
def session_maker(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    created = []

    def make(paths):
        s = Session(
            music,
            paths,
            presets=PresetStore(),
            settings=lambda: default_settings(),
            journal=Journal(tmp_path / "journal"),
        )
        created.append(s)
        return s

    yield make
    for s in created:
        s.close()


def test_queue_append_tracks_and_stream(tmp_path, session_maker):
    music = tmp_path / "music"
    p1 = music / "track1.mp3"
    p2 = music / "track2.mp3"
    p1.touch()
    p2.touch()

    s = session_maker([p1])
    assert s.total == 1
    assert len(s.queue) == 1
    assert s.current.path == p1

    p3 = music / "track3.mp3"
    p3.touch()
    added = s.append_tracks([p2, p3, p1])  # p1 duplicate should be skipped
    assert len(added) == 2
    assert s.total == 3
    assert len(s.queue) == 3
    assert [t.path for t in s.queue] == [p1, p2, p3]


def test_queue_search_cached_token(tmp_path, session_maker):
    music = tmp_path / "music"
    (music / "House").mkdir()
    (music / "Techno").mkdir()
    p1 = music / "House" / "Deep Vibe.mp3"
    p2 = music / "Techno" / "Dark Kick.mp3"
    p3 = music / "House" / "Vocal Soul.mp3"
    for p in (p1, p2, p3):
        p.touch()

    s = session_maker([p1, p2, p3])
    # Search is upcoming tracks (queue[1:])
    res = s.queue_slice(q="techno")
    assert res["total"] == 1
    assert res["items"][0]["name"] == "Dark Kick.mp3"

    res_vocal = s.queue_slice(q="house vocal")
    assert res_vocal["total"] == 1
    assert res_vocal["items"][0]["name"] == "Vocal Soul.mp3"


def test_queue_play_next_and_reorder(tmp_path, session_maker):
    music = tmp_path / "music"
    tracks = [music / f"t_{i}.mp3" for i in range(5)]
    for p in tracks:
        p.touch()

    s = session_maker(tracks)
    # Move 4th track to be next
    target_id = s.queue[3].id
    s.play_next(target_id)
    assert s.queue[1].id == target_id
