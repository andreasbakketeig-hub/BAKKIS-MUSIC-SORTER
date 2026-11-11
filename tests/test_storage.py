"""Tests for EncryptedStorage (bakkis.db) and CommonCrypto AES-256-CBC + HMAC-SHA256 encryption."""

from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.storage

from bakkis_sorter.storage import Aes256Cipher, CryptoError, EncryptedStorage


def test_aes256_cipher_roundtrip_and_tamper_detection() -> None:
    key = secrets.token_bytes(32)
    cipher = Aes256Cipher(key)

    payload = {"artist": "Secret Artist", "title": "Unreleased Promo VIP", "tracks": [1, 2, 3]}
    token = cipher.encrypt_json(payload)
    assert token.startswith("enc:v1:")
    assert "Secret Artist" not in token
    assert "Unreleased Promo VIP" not in token

    recovered = cipher.decrypt_json(token)
    assert recovered == payload

    # Flipping a character in ciphertext must fail HMAC verification
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(CryptoError):
        cipher.decrypt_json(tampered)

    # Wrong key must fail HMAC verification
    other_cipher = Aes256Cipher(secrets.token_bytes(32))
    with pytest.raises(CryptoError):
        other_cipher.decrypt_json(token)


def test_encrypted_sqlite_hides_plaintext_on_disk(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    secret_promo = {
        "promo": {"id": 999111, "name": "Confidential Dubplate EP"},
        "tracks": [{"id": 555111, "name": "Top Secret Club Mix", "artist_name": "DJ Anonymous"}],
    }
    store.put_promo_cache("inflyte", 999111, secret_promo)
    store.put_kv("trackstack_context", {"workspace_id": "ws-secret-uuid-1234", "inbox_id": "inbox-uuid-5678"})

    # Read back through EncryptedStorage
    assert store.get_promo_cache("inflyte", 999111) == secret_promo
    assert store.get_kv("trackstack_context")["workspace_id"] == "ws-secret-uuid-1234"

    # Inspect raw SQLite rows directly to verify zero plaintext leakage
    conn = sqlite3.connect(str(db_file))
    raw_rows = conn.execute("SELECT ciphertext FROM promo_cache").fetchall()
    conn.close()
    assert len(raw_rows) == 1
    raw_ct = raw_rows[0][0]
    assert raw_ct.startswith("enc:v1:")
    assert "Confidential Dubplate EP" not in raw_ct
    assert "Top Secret Club Mix" not in raw_ct
    store.close()


def test_legacy_json_migration_and_media_cleanup(tmp_path: Path) -> None:
    cache_dir = tmp_path / "inflyte_cache"
    cache_dir.mkdir(parents=True)
    legacy_json = cache_dir / "release_123456.json"
    legacy_payload = {
        "promo": {"id": 123456, "name": "Migrated Release"},
        "tracks": [{"id": 777, "name": "Track One"}],
    }
    legacy_json.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Initialize EncryptedStorage in tmp_path -> should migrate release_123456.json and delete the loose file
    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=secrets.token_bytes(32))
    assert not legacy_json.exists()
    assert store.get_promo_cache("inflyte", 123456) == legacy_payload

    # Create dummy cached preview audio and cover files
    preview_mp3 = cache_dir / "track_inflyte_777.mp3"
    cover_jpg = cache_dir / "cover_inflyte_123456.jpg"
    preview_mp3.write_bytes(b"ID3-dummy-audio")
    cover_jpg.write_bytes(b"JPEG-dummy-cover")

    # Recording a review removes promo_cache entry, records review, and deletes temporary media files
    store.record_review(
        "inflyte",
        123456,
        "rate_5",
        {"rating": 5, "comment": "Banger!"},
        media_paths=[preview_mp3, cover_jpg],
    )
    assert store.is_reviewed("inflyte", 123456)
    assert "123456" in store.get_reviewed_keys("inflyte")
    assert store.get_promo_cache("inflyte", 123456) is None
    assert not preview_mp3.exists()
    assert not cover_jpg.exists()
    store.close()


def test_database_stats_and_vacuum(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    # Insert sample records across tables
    store.record_log(
        level="ERROR",
        module="test_module",
        message="Simulated test error",
        exception_type="ValueError",
        traceback="Traceback (most recent call last):\n  File 'test.py', line 1, in <module>\nValueError: boom",
    )
    store.put_peaks("hash123456", [0.1, 0.5, 0.9])
    store.record_review("inflyte", "999", "rate_5", {"rating": 5})

    stats = store.get_database_stats()
    assert stats["file_size_bytes"] > 0
    assert stats["counts"]["app_logs"] == 1
    assert stats["counts"]["waveform_peaks"] == 1
    assert stats["counts"]["promo_reviews"] == 1
    assert "reclaimable_bytes" in stats
    assert stats["page_size"] > 0

    # Test vacuum operation
    vac_res = store.vacuum()
    assert "before_bytes" in vac_res
    assert "after_bytes" in vac_res
    assert "reclaimed_bytes" in vac_res

    # Stats after vacuum should be intact
    post_stats = store.get_database_stats()
    assert post_stats["counts"]["app_logs"] == 1
    assert post_stats["counts"]["waveform_peaks"] == 1

    # Exact level filter in get_logs
    err_logs = store.get_logs(level="ERROR")
    assert len(err_logs) == 1
    assert err_logs[0]["message"] == "Simulated test error"

    info_logs = store.get_logs(level="INFO")
    assert len(info_logs) == 0

    all_logs = store.get_logs(min_level="ALL")
    assert len(all_logs) == 1

    store.close()


def test_promo_track_signatures_and_retention(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    # 1. Record signatures
    store.record_track_signature(
        signature="fisher:losing it",
        artist="Fisher",
        title="Losing It",
        decision="rate_5",
        rating=5,
        source="inflyte",
        promo_name="Catch & Release EP",
    )
    store.record_track_signature(
        signature="boris brejcha:gravity",
        artist="Boris Brejcha",
        title="Gravity",
        decision="dismiss",
        rating=None,
        source="trackstack",
        promo_name="Gravity Single",
        reviewed_at=100_000.0,  # Old timestamp
    )

    # 2. Lookup matches
    match1 = store.get_track_signature_match("fisher:losing it")
    assert match1 is not None
    assert match1["rating"] == 5
    assert match1["decision"] == "rate_5"
    assert match1["source"] == "inflyte"
    assert match1["promo_name"] == "Catch & Release EP"

    match2 = store.get_track_signature_match("boris brejcha:gravity")
    assert match2 is not None
    assert match2["decision"] == "dismiss"

    assert store.get_track_signature_match("unknown:track") is None

    # 3. Stats reflect signature count
    stats = store.get_database_stats()
    assert stats["counts"]["promo_track_signatures"] == 2

    # 4. Pruning removes old signatures
    pruned = store.prune_track_signatures(retention_days=30)
    assert pruned == 1  # boris brejcha:gravity from year 1970 pruned
    assert store.get_track_signature_match("boris brejcha:gravity") is None
    assert store.get_track_signature_match("fisher:losing it") is not None

    store.close()


def test_waveform_peaks_blob_storage_and_backwards_compat(tmp_path: Path) -> None:
    import base64

    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    peaks_raw = bytes(range(256)) * 3 + bytes(range(32))  # 800 bytes
    store.put_peaks_bytes("hash_800", peaks_raw)

    # 1. Binary retrieval
    retrieved = store.get_peaks_bytes("hash_800")
    assert retrieved == peaks_raw
    assert isinstance(retrieved, bytes)

    # 2. String/Base64 backward compatibility
    b64_str = store.get_peaks("hash_800")
    assert isinstance(b64_str, str)
    assert base64.b64decode(b64_str) == peaks_raw

    # 3. Reading legacy text rows in DB
    with store._lock:
        store._conn.execute(
            "INSERT INTO waveform_peaks (file_hash, peaks, cached_at) VALUES (?, ?, ?)",
            ("hash_legacy", "AQIDBA==", 1000.0),
        )
        store._conn.commit()

    assert store.get_peaks_bytes("hash_legacy") == b"\x01\x02\x03\x04"
    assert store.get_peaks("hash_legacy") == "AQIDBA=="

    # 4. Updating on conflict
    store.put_peaks_bytes("hash_800", b"new_peak_data")
    assert store.get_peaks_bytes("hash_800") == b"new_peak_data"

    store.close()


def test_waveform_peaks_prune_lru_cap(tmp_path: Path) -> None:
    import sqlite3

    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    with store._lock:
        for i in range(15):
            store._conn.execute(
                "INSERT INTO waveform_peaks (file_hash, peaks, cached_at) VALUES (?, ?, ?)",
                (f"h_{i:02d}", sqlite3.Binary(b"peak"), 1000.0 + i),
            )
        store._conn.commit()

    assert store.prune_peaks(max_entries=10) == 5
    # Oldest (h_00..h_04) pruned, newest (h_05..h_14) retained
    assert store.get_peaks_bytes("h_00") is None
    assert store.get_peaks_bytes("h_04") is None
    assert store.get_peaks_bytes("h_05") == b"peak"
    assert store.get_peaks_bytes("h_14") == b"peak"

    store.close()


def test_promo_review_history(tmp_path: Path) -> None:
    db_file = tmp_path / "bakkis.db"
    store = EncryptedStorage(db_path=db_file, master_key=secrets.token_bytes(32))

    store.record_review(
        "inflyte",
        101,
        "rate_5",
        {
            "promo_name": "Summer Anthem EP",
            "artist": "Solaris",
            "title": "Sunlight (Original Mix)",
            "rating": 5,
            "comment": "Absolute dancefloor weapon!",
        },
    )
    store.record_review(
        "trackstack",
        202,
        "rate_4",
        {
            "promo_name": "Deep Space",
            "artist": "Cosmo",
            "title": "Nebula",
            "rating": 4,
            "comment": "Nice groove.",
            "downloaded": ["/path/to/Nebula.wav"],
        },
    )
    store.record_review(
        "inflyte",
        303,
        "dismiss",
        {
            "promo_name": "Generic Beats",
            "artist": "DJ Dull",
            "title": "Boring Beat",
        },
    )

    reviews, total = store.get_promo_review_history()
    assert total == 3
    assert len(reviews) == 3
    first = next(r for r in reviews if r["promo_key"] == "101")
    assert first["source"] == "inflyte"
    assert first["artist"] == "Solaris"
    assert first["title"] == "Sunlight (Original Mix)"
    assert first["rating"] == 5
    assert first["comment"] == "Absolute dancefloor weapon!"

    ts_reviews, ts_total = store.get_promo_review_history(source="trackstack")
    assert ts_total == 1
    assert ts_reviews[0]["artist"] == "Cosmo"
    assert len(ts_reviews[0]["downloaded"]) == 1

    searched, s_total = store.get_promo_review_history(search="weapon")
    assert s_total == 1
    assert searched[0]["promo_key"] == "101"

    store.close()



