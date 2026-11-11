"""Tests for PLN-07: In-app SQLite promo comments storage, configurable ratings, and sanitization."""

import pytest
import sqlite3
import secrets
from pathlib import Path
from unittest.mock import MagicMock

from bakkis_sorter.backend import AppBackend
from bakkis_sorter.promos.comments import CommentPool, sanitize_comment
from bakkis_sorter.promos.models import InflytePromo, InflyteTrack
from bakkis_sorter.promos.session import InflyteSession
from bakkis_sorter.storage import EncryptedStorage


@pytest.fixture
def tmp_storage(tmp_path):
    db_file = tmp_path / "bakkis_test.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))
    return store


@pytest.mark.storage
def test_promo_comments_storage_roundtrip(tmp_storage):
    """Test storing and retrieving promo comments directly from SQLite."""
    # Table should exist
    pools = {
        5: ["Banger! Straight into the bag", "Absolute weapon 🔥"],
        4: ["Nice groove, will test out", "Solid club tool 🎧"],
    }
    tmp_storage.set_promo_comments(pools)

    loaded = tmp_storage.get_promo_comments()
    assert loaded[5] == pools[5]
    assert loaded[4] == pools[4]

    # Update comments
    updated_pools = {
        5: ["New favorite 🚀"],
        4: ["Testing next weekend"],
    }
    tmp_storage.set_promo_comments(updated_pools)
    loaded_updated = tmp_storage.get_promo_comments()
    assert loaded_updated[5] == ["New favorite 🚀"]
    assert loaded_updated[4] == ["Testing next weekend"]


@pytest.mark.storage
def test_promo_comments_migration_from_default(tmp_storage):
    """Verify that an empty database table automatically populates with default comments."""
    loaded = tmp_storage.get_promo_comments()
    assert len(loaded[5]) > 0
    assert len(loaded[4]) > 0
    assert any("weapon" in c.lower() or "groove" in c.lower() for c in loaded[5])


@pytest.mark.storage
def test_promo_comments_encoding_and_unsupported_characters(tmp_storage):
    """Verify emojis, European diacritics, smart quotes, null bytes, and surrogates."""
    # 1. Emojis and diacritics preserved
    rich_comment = "Banger track! 🔥🎶 Très stylé, déjà joué à Düsseldorf — 100% banger!"
    sanitized = sanitize_comment(rich_comment)
    assert sanitized == rich_comment

    # 2. Null bytes and control characters stripped
    dirty_comment = "Bad\x00data\x07with\x1bescape\x08codes"
    clean = sanitize_comment(dirty_comment)
    assert "\x00" not in clean
    assert "\x07" not in clean
    assert clean == "Baddatawithescapecodes"

    # 3. Lone surrogate handling without crashing
    surrogate_text = "Testing\ud800surrogates"
    safe = sanitize_comment(surrogate_text)
    assert isinstance(safe, str)
    assert "\ud800" not in safe

    # 4. Length clamping to 500 chars
    long_comment = "A" * 650
    clamped = sanitize_comment(long_comment, max_len=500)
    assert len(clamped) == 500

    # 5. Persist rich unicode in database
    pools = {5: ["🔥 Banger! «Très bon» “Solid” — Düsseldorf"], 4: ["Nice ü ø å"]}
    tmp_storage.set_promo_comments(pools)
    loaded = tmp_storage.get_promo_comments()
    assert loaded[5] == pools[5]
    assert loaded[4] == pools[4]


@pytest.mark.storage
def test_promo_comments_sql_injection_resilience(tmp_storage):
    """Verify that malicious SQL injection attempts in comments or ratings are safely handled."""
    malicious_payloads = [
        "'); DROP TABLE promo_comments; --",
        "' OR '1'='1",
        "admin'--",
        "1; DELETE FROM promo_comments;",
        "'; UPDATE promo_comments SET comment='hacked' WHERE 1=1; --",
    ]
    # Set comments containing attack payloads
    tmp_storage.set_promo_comments({5: malicious_payloads, 4: ["normal comment"]})

    # Verify table is still intact and payloads were saved as literal text
    loaded = tmp_storage.get_promo_comments()
    assert len(loaded[5]) == len(malicious_payloads)
    for original, stored in zip(malicious_payloads, loaded[5]):
        assert original == stored

    # Attempt illegal rating types/values (must be 1..5)
    tmp_storage.set_promo_comments({"invalid_sql'; DROP TABLE promo_comments; --": ["fail"]})
    tmp_storage.set_promo_comments({99: ["out of range"]})
    # Table should still exist and comments unchanged
    loaded_after = tmp_storage.get_promo_comments()
    assert len(loaded_after[5]) == len(malicious_payloads)


@pytest.mark.promos
def test_comment_pool_reads_from_storage(tmp_storage):
    """Verify CommentPool loads comments from SQLite storage and rotates properly."""
    custom_pools = {
        5: ["Custom banger 1", "Custom banger 2"],
        4: ["Custom keep 1"],
    }
    tmp_storage.set_promo_comments(custom_pools)

    pool = CommentPool(storage=tmp_storage)
    pools = pool.load_pools()
    assert pools[5] == custom_pools[5]
    assert pools[4] == custom_pools[4]

    # Pick comments
    pick_fav = pool.pick(5)
    assert pick_fav in custom_pools[5]
    pick_keep = pool.pick(4)
    assert pick_keep == "Custom keep 1"


def _make_promo(release_id: int, name: str, num_tracks: int = 1) -> InflytePromo:
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
        summary="",
        tracks=tracks,
    )


@pytest.mark.promos
def test_session_uses_custom_ratings(tmp_storage):
    """Verify InflyteSession uses configurable ratings (e.g. 5/5 -> 4/5, 4/5 -> 3/5)."""
    custom_settings = {
        "promo_rating_favorite": 4,  # Custom: Favorite sends 4/5
        "promo_rating_keep": 3,      # Custom: Keep sends 3/5
    }

    mock_bridge = MagicMock()
    mock_bridge.storage = tmp_storage
    mock_bridge.submit_feedback.return_value = {"ok": True}

    pool = CommentPool(storage=tmp_storage)
    promo1 = _make_promo(123, "Test Release 1")

    session = InflyteSession(
        [promo1],
        bridge=mock_bridge,
        storage=tmp_storage,
        presets=None,
        settings=lambda: custom_settings,
        comment_pool=pool,
    )

    # slot 2 is Favorite / Feeling this (Up arrow)
    cur = session.current
    assert cur is not None
    session.sort(cur.id, "swipe", slot=2)
    session.flush()

    # Verify bridge was called with custom rating = 4 (not hardcoded 5)
    mock_bridge.submit_feedback.assert_called_once()
    kwargs = mock_bridge.submit_feedback.call_args.kwargs
    assert kwargs["rating"] == 4
    assert kwargs["release_id"] == 123
    assert kwargs["favourite_track_id"] == 12301
    assert isinstance(kwargs["comments"], str)

    # Now test slot 0: Keep / Maybe (Right arrow)
    mock_bridge.reset_mock()
    promo2 = _make_promo(789, "Test Release 2")
    session.append_promo(promo2)

    cur2 = session.current
    assert cur2 is not None
    session.sort(cur2.id, "swipe", slot=0)
    session.flush()

    # Verify bridge was called with custom rating = 3 (not hardcoded 4)
    mock_bridge.submit_feedback.assert_called_once()
    kwargs2 = mock_bridge.submit_feedback.call_args.kwargs
    assert kwargs2["rating"] == 3
    assert kwargs2["release_id"] == 789
    assert kwargs2["favourite_track_id"] == 78901
    session.close()


@pytest.mark.promos
def test_backend_promo_comments_api(tmp_path, monkeypatch):
    """Test api_promo_comments_get and api_promo_comments_save through AppBackend."""
    monkeypatch.setenv("BAKKIS_CONFIG_DIR", str(tmp_path))
    st = EncryptedStorage(tmp_path / "test_api.db", master_key=secrets.token_bytes(32))
    backend = AppBackend()
    backend.storage = st

    # 1. Get initial comments
    init_res = backend.api_promo_comments_get()
    assert "comments_5" in init_res
    assert "comments_4" in init_res
    assert init_res["promo_rating_favorite"] == 5
    assert init_res["promo_rating_keep"] == 4

    # 2. Save new comments & custom ratings
    new_c5 = ["Top shelf banger 🔥", "Absolute heat"]
    new_c4 = ["Solid club groove"]
    save_res = backend.api_promo_comments_save(
        comments_5=new_c5,
        comments_4=new_c4,
        promo_rating_favorite=4,
        promo_rating_keep=3,
    )
    assert save_res["ok"] is True
    assert save_res["promo_rating_favorite"] == 4
    assert save_res["promo_rating_keep"] == 3

    # 3. Retrieve updated comments
    get_res = backend.api_promo_comments_get()
    assert get_res["comments_5"] == new_c5
    assert get_res["comments_4"] == new_c4
    assert get_res["promo_rating_favorite"] == 4
    assert get_res["promo_rating_keep"] == 3

    backend.close()
    st.close()
