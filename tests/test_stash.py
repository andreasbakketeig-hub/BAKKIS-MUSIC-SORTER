"""Unit and integration tests for Offline Stash Mode (Flight & Travel)."""

import secrets
import threading
import time
from pathlib import Path
import pytest

pytestmark = pytest.mark.promos

from bakkis_sorter.inflyte.models import InflyteAction, InflytePromo, InflyteTrack
from bakkis_sorter.inflyte.session import InflyteSession
from bakkis_sorter.storage import EncryptedStorage
from tests.audio_fixtures import make_mp3


def make_dummy_promo(release_id: int, name: str, tracks_count: int = 2) -> InflytePromo:
    tracks = [
        InflyteTrack(
            id=f"track_{release_id}_{i}",
            inflyte_track_id=1000 + i,
            release_id=release_id,
            name=f"Track {i + 1}",
            artist_name="Test Artist",
            duration=180.0,
            track_index=i,
            track_total=tracks_count,
            bpm=126.0,
        )
        for i in range(tracks_count)
    ]
    return InflytePromo(
        release_id=release_id,
        name=name,
        sender_label="Defected",
        release_date="2026-11-01",
        thumbnail_url="",
        full_image_url="",
        summary="Promo summary",
        tracks=tracks,
        source="inflyte",
    )


def test_storage_offline_reviews_queue_and_sync(tmp_path):
    st = EncryptedStorage(tmp_path / "test.db", master_key=secrets.token_bytes(32))

    # Initially empty
    assert st.get_offline_review_count() == 0
    assert st.get_pending_offline_reviews() == []

    # Queue an offline review
    dummy_file = tmp_path / "test_preview.mp3"
    dummy_file.write_bytes(b"dummy audio")

    rev_id = st.queue_offline_review(
        source="inflyte",
        promo_key="101",
        release_id=101,
        decision="rate_5",
        rating=5,
        comment="Great track!",
        favourite_track_id=1000,
        promo_name="Release 101",
        payload={"rating": 5, "comment": "Great track!"},
        media_paths=[dummy_file],
    )
    assert rev_id > 0
    assert st.get_offline_review_count() == 1

    pending = st.get_pending_offline_reviews()
    assert len(pending) == 1
    assert pending[0]["release_id"] == 101
    assert pending[0]["decision"] == "rate_5"
    assert pending[0]["rating"] == 5
    assert pending[0]["comment"] == "Great track!"
    assert pending[0]["payload"] == {"rating": 5, "comment": "Great track!"}

    # Mark synced: transfers to promo_reviews and deletes media paths
    st.mark_offline_review_synced(rev_id)
    assert st.get_offline_review_count() == 0
    assert st.is_reviewed("inflyte", "101")
    assert not dummy_file.exists()  # media path cleaned up

    st.close()


def test_promo_models_to_from_dict():
    p = make_dummy_promo(202, "Summer EP", tracks_count=3)
    p_dict = p.to_dict()

    assert p_dict["release_id"] == 202
    assert p_dict["name"] == "Summer EP"
    assert len(p_dict["tracks"]) == 3
    assert p_dict["tracks"][0]["name"] == "Track 1"

    reconstructed = InflytePromo.from_dict(p_dict)
    assert reconstructed.release_id == 202
    assert reconstructed.name == "Summer EP"
    assert len(reconstructed.tracks) == 3
    assert reconstructed.tracks[0].name == "Track 1"
    assert reconstructed.tracks[0].bpm == 126.0


class DummyBridge:
    def __init__(self):
        self.submitted = []
        self.dismissed = []

    def submit_feedback(self, release_id, favourite_track_id, rating, comments):
        self.submitted.append((release_id, favourite_track_id, rating, comments))

    def dismiss_promo(self, release_id):
        self.dismissed.append(release_id)


def test_inflyte_session_offline_mode_queuing(tmp_path):
    st = EncryptedStorage(tmp_path / "test.db", master_key=secrets.token_bytes(32))
    p1 = make_dummy_promo(301, "Album A")
    p2 = make_dummy_promo(302, "Album B")

    bridge = DummyBridge()
    sess = InflyteSession(
        [p1, p2],
        bridge=bridge,
        storage=st,
        presets=None,
        settings=lambda: {},
    )

    # Enable offline mode
    sess.offline_mode = True

    # Taking an action should queue offline rather than calling bridge or failing
    cur = sess.current
    assert cur is not None
    action = sess.sort(cur.id, "swipe", slot=2)
    assert action is not None

    # Wait briefly for worker action execution
    time.sleep(0.15)

    assert action.status == "queued_offline"
    assert st.get_offline_review_count() == 1
    assert len(bridge.submitted) == 0  # didn't hit network

    # Test sync when back online
    res = sess.sync_offline_reviews()
    assert res["synced"] == 1
    assert res["remaining"] == 0
    assert len(bridge.submitted) == 1
    assert bridge.submitted[0][0] == 301  # release_id 301

    st.close()


def test_stash_promos_and_offline_recovery(tmp_path):
    st = EncryptedStorage(tmp_path / "test.db", master_key=secrets.token_bytes(32))
    p1 = make_dummy_promo(401, "Stash Album 1")
    p2 = make_dummy_promo(402, "Stash Album 2")

    bridge = DummyBridge()
    sess = InflyteSession(
        [p1, p2],
        bridge=bridge,
        storage=st,
        presets=None,
        settings=lambda: {},
    )

    # Trigger stash
    state = sess.stash_promos(target_count=2)
    assert state["status"] in ("stashing", "ready")

    # Wait for stash worker to complete
    if sess._stash_thread:
        sess._stash_thread.join(timeout=3.0)

    final_state = sess.get_stash_state()
    assert final_state["status"] == "ready"
    assert final_state["completed_promos"] == 2

    # Verify stashed promos stored in SQLite promo_cache
    stashed = st.get_all_stashed_promos()
    assert len(stashed) == 2
    names = {s["name"] for s in stashed}
    assert "Stash Album 1" in names
    assert "Stash Album 2" in names

    st.close()


def test_stash_backend_api(tmp_path):
    from bakkis_sorter.api import AppBackend

    st = EncryptedStorage(tmp_path / "test.db", master_key=secrets.token_bytes(32))
    p1 = make_dummy_promo(501, "API Stash Album")
    backend = AppBackend()
    backend.storage = st

    # Attach dummy session
    sess = InflyteSession([p1], bridge=DummyBridge(), storage=st, presets=backend.presets, settings=lambda: {})
    backend.session = sess

    # Test api_toggle_offline_mode
    res = backend.api_toggle_offline_mode(True)
    assert res["offline_mode"] is True
    assert sess.offline_mode is True

    # Test api_stash_promos
    stash_res = backend.api_stash_promos(count=1)
    assert stash_res["status"] in ("stashing", "ready")

    # Test api_stash_status
    status = backend.api_stash_status()
    assert "status" in status
    assert "queued_offline" in status

    # Test api_sync_offline_reviews
    sync_res = backend.api_sync_offline_reviews()
    assert "synced" in sync_res
    assert "remaining" in sync_res

    backend.close()
    st.close()
