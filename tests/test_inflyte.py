"""Tests for Inflyte Promo Box integration: CommentPool ([5/5] & [4/5]) and InflyteSession multi-track flow."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

pytestmark = pytest.mark.promos

from bakkis_sorter.config import PresetStore, default_settings
from bakkis_sorter.inflyte import CommentPool, InflytePromo, InflyteSession, InflyteTrack, parse_comments_text
from bakkis_sorter.storage import EncryptedStorage


class FakeInflyteBridge:
    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.dismissed: list[int] = []
        self.played: list[tuple[int, int]] = []

    def submit_feedback(self, release_id: int, favourite_track_id: int, rating: int, comments: str) -> dict:
        self.submitted.append(
            {
                "release_id": release_id,
                "favourite_track_id": favourite_track_id,
                "rating": rating,
                "comments": comments,
            }
        )
        return {"ok": True}

    def dismiss_promo(self, release_id: int) -> dict:
        self.dismissed.append(release_id)
        return {"ok": True}

    def mark_track_played(self, release_id: int, inflyte_track_id: int) -> None:
        self.played.append((release_id, inflyte_track_id))


def _make_promo(release_id: int, name: str, num_tracks: int) -> InflytePromo:
    tracks = [
        InflyteTrack(
            id=f"t-{release_id}-{i + 1}",
            inflyte_track_id=release_id * 100 + (i + 1),
            release_id=release_id,
            name=f"{name} Track {i + 1}",
            artist_name="Test Artist",
            duration=300.0,
            track_index=i,
            track_total=num_tracks,
        )
        for i in range(num_tracks)
    ]
    return InflytePromo(
        release_id=release_id,
        name=name,
        sender_label="Test Label",
        release_date="Nov 2026",
        thumbnail_url="",
        full_image_url="",
        summary="Test promo",
        tracks=tracks,
    )


def test_comment_pool_delineates_5_and_4(tmp_path: Path) -> None:
    txt = """
    # Comment file
    [5/5]
    Five Star A
    Five Star B

    [4/5]
    Four Star X
    Four Star Y
    """
    p = tmp_path / "promo_comments.txt"
    p.write_text(txt, encoding="utf-8")

    pools = parse_comments_text(txt)
    assert pools[5] == ["Five Star A", "Five Star B"]
    assert pools[4] == ["Four Star X", "Four Star Y"]

    cp = CommentPool(p)
    picks_5 = [cp.pick(5) for _ in range(6)]
    picks_4 = [cp.pick(4) for _ in range(6)]
    assert all(x in ("Five Star A", "Five Star B") for x in picks_5)
    assert all(x in ("Four Star X", "Four Star Y") for x in picks_4)
    # Never repeats immediately back-to-back when pool has >=2 items
    for i in range(1, len(picks_5)):
        assert picks_5[i] != picks_5[i - 1]
    for i in range(1, len(picks_4)):
        assert picks_4[i] != picks_4[i - 1]


def test_multitrack_skip_and_feedback_flow(tmp_path: Path) -> None:
    p = tmp_path / "promo_comments.txt"
    p.write_text("[5/5]\nBanger 5\n[4/5]\nSolid 4\n", encoding="utf-8")
    pool = CommentPool(p)
    bridge = FakeInflyteBridge()
    presets = PresetStore(tmp_path / "presets.json")

    promo1 = _make_promo(101, "Five Track EP", 5)
    promo2 = _make_promo(102, "Three Track EP", 3)
    promo3 = _make_promo(103, "Single Promo", 1)
    promo4 = _make_promo(104, "Reject Promo", 2)

    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=secrets.token_bytes(32))
    sess = InflyteSession(
        [promo1, promo2, promo3, promo4],
        bridge=bridge,
        storage=store,
        presets=presets,
        settings=default_settings,
        comment_pool=pool,
    )

    # 1. Starts on Track 1 of Promo 1
    assert sess.current is not None
    assert sess.current.id == "t-101-1"
    assert sess.current.track_index == 0

    # 2. Pressing skip (↓) steps to Track 2 of Promo 1 (not next promo)
    sess.skip("t-101-1")
    assert sess.current.id == "t-101-2"
    sess.skip("t-101-2")
    assert sess.current.id == "t-101-3"

    # 3. Pressing Feeling it (↑, slot 2) on Track 3 of 5 submits 5/5 for Promo 1 and jumps to Track 1 of Promo 2
    sess.sort("t-101-3", "swipe", 2)
    sess.flush()
    assert len(bridge.submitted) == 1
    assert bridge.submitted[0] == {
        "release_id": 101,
        "favourite_track_id": 10103,
        "rating": 5,
        "comments": "Banger 5",
    }
    assert sess.current is not None
    assert sess.current.id == "t-102-1"

    # 4. On Promo 2 (3 tracks), skip through all 3 tracks; on the 3rd track, skip (↓) skips the whole promo for now
    sess.skip("t-102-1")
    assert sess.current.id == "t-102-2"
    sess.skip("t-102-2")
    assert sess.current.id == "t-102-3"
    sess.skip("t-102-3")
    # Now on Track 1 of Promo 3, and no feedback was submitted for Promo 2
    assert sess.current.id == "t-103-1"
    assert len(bridge.submitted) == 1

    # 5. On Promo 3, pressing Maybe (→, slot 0) submits 4/5 with [4/5] comment and moves to Promo 4
    sess.sort("t-103-1", "swipe", 0)
    sess.flush()
    assert len(bridge.submitted) == 2
    assert bridge.submitted[1] == {
        "release_id": 103,
        "favourite_track_id": 10301,
        "rating": 4,
        "comments": "Solid 4",
    }
    assert sess.current.id == "t-104-1"

    # 6. On Promo 4, pressing Not for me (←, slot 1) dismisses without feedback
    sess.sort("t-104-1", "swipe", 1)
    sess.flush()
    assert bridge.dismissed == [104]
    assert sess.current is None
    sess.close()


def test_inflyte_duplicate_review_tracking(tmp_path: Path):
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    bridge = FakeInflyteBridge()
    presets = PresetStore(dated_default=False)

    p1 = _make_promo(201, "Summer Groove", 2)
    sess = InflyteSession(
        [p1],
        bridge=bridge,
        storage=store,
        presets=presets,
        settings=default_settings,
    )

    # 1. Initially no duplicate review flag
    meta = sess.track_meta("t-201-1")
    assert meta.get("duplicate_review") is None

    # 2. Rate 5/5 on Track 1
    sess.sort("t-201-1", "swipe", 2)
    sess.flush()
    sess.close()

    # 3. Promoter re-sends the track weeks later under a new promo ID (202)
    p2 = _make_promo(202, "Summer Groove", 1)
    sess2 = InflyteSession(
        [p2],
        bridge=bridge,
        storage=store,
        presets=presets,
        settings=default_settings,
    )

    # 4. Must flag the track with previous review info
    snap = sess2.snapshot()
    assert snap["current"]["duplicate_review"] is not None
    assert snap["current"]["duplicate_review"]["decision"] == "rate_5"
    assert snap["current"]["duplicate_review"]["rating"] == 5

    meta2 = sess2.track_meta("t-202-1")
    assert meta2["duplicate_review"] is not None
    assert meta2["duplicate_review"]["decision"] == "rate_5"

    sess2.close()
    store.close()


def test_inflyte_bridge_auth_persistence(tmp_path: Path) -> None:
    from bakkis_sorter.promos.inflyte.bridge import InflyteBridge

    key = secrets.token_bytes(32)
    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=key)

    # 1. Fresh database without reviews or cached conn defaults to False
    b1 = InflyteBridge(storage=store)
    st1 = b1.check_auth_status()
    assert st1["connected"] is False

    # 2. When bridge detects ready state via JS evaluation, saves to storage
    class _DummyWin:
        def __init__(self) -> None:
            self.hidden = False

        def hide(self) -> None:
            self.hidden = True

    dummy_win = _DummyWin()
    b1._win = dummy_win
    b1._login_window_shown = True

    def fake_eval_js(script: str, timeout: float = 15.0, *, visible: bool = False):
        return {
            "href": "https://inflyteapp.com/promos",
            "ready": True,
            "isLogin": False,
        }

    b1._eval_js = fake_eval_js  # type: ignore[method-assign]
    st2 = b1.check_auth_status()
    assert st2["connected"] is True
    assert dummy_win.hidden is True
    assert store.get_kv("inflyte_connected") is True

    # 3. Subsequent bridge instance (e.g. on app restart) loads connected status from storage
    b2 = InflyteBridge(storage=store)
    st3 = b2.check_auth_status()
    assert st3["connected"] is True
    assert st3["window_open"] is False

    # 4. If login screen is encountered, status resets to False
    b2._win = dummy_win

    def fake_eval_js_login(script: str, timeout: float = 15.0, *, visible: bool = False):
        return {
            "href": "https://inflyteapp.com/users/sign_in",
            "ready": False,
            "isLogin": True,
        }

    b2._eval_js = fake_eval_js_login  # type: ignore[method-assign]
    st4 = b2.check_auth_status()
    assert st4["connected"] is False
    assert store.get_kv("inflyte_connected") is False

    store.close()


