"""Encrypted SQLite storage (``bakkis.db``) backed by macOS Keychain + CommonCrypto AES-256."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from ..config import config_dir
from .analysis import AnalysisStorageMixin
from .crypto import (
    Aes256Cipher,
    CIPHER_PREFIX,
    CryptoError,
    DB_FILENAME,
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    get_or_create_master_key,
)
from .comments import CommentStorageMixin
from .diagnostics import DiagnosticStorageMixin
from .library import LibraryStorageMixin
from .offline_queue import OfflineQueueStorageMixin
from .reviews import ReviewStorageMixin, cleanup_media_files
from .signatures import SignatureStorageMixin


class EncryptedStorage(
    ReviewStorageMixin,
    SignatureStorageMixin,
    OfflineQueueStorageMixin,
    CommentStorageMixin,
    AnalysisStorageMixin,
    LibraryStorageMixin,
    DiagnosticStorageMixin,
):
    """Thread-safe encrypted SQLite store (`bakkis.db`) in WAL mode."""

    def __init__(self, db_path: Optional[Path] = None, master_key: Optional[bytes] = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else (config_dir() / DB_FILENAME)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        key = master_key or get_or_create_master_key(
            db_dir=self.db_path.parent if db_path is not None else None
        )
        self.cipher = Aes256Cipher(key)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15.0)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._init_schema()
        self._migrate_legacy_json_files()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA temp_store=MEMORY")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_cache (
                    source TEXT NOT NULL,
                    promo_key TEXT NOT NULL,
                    ciphertext TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (source, promo_key)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_reviews (
                    source TEXT NOT NULL,
                    promo_key TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    ciphertext TEXT NOT NULL,
                    reviewed_at REAL NOT NULL,
                    PRIMARY KEY (source, promo_key)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    ciphertext TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS waveform_peaks (
                    file_hash TEXT PRIMARY KEY,
                    peaks BLOB NOT NULL,
                    cached_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_waveform_cached_at
                ON waveform_peaks (cached_at)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS copy_index (
                    src_path TEXT PRIMARY KEY,
                    dest_path TEXT NOT NULL,
                    copied_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    action_time REAL NOT NULL,
                    mode TEXT,
                    op TEXT,
                    src_path TEXT,
                    dest_path TEXT,
                    label TEXT,
                    preset_name TEXT,
                    session_id TEXT
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_time ON session_history(action_time)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    level TEXT NOT NULL,
                    module TEXT NOT NULL,
                    message TEXT NOT NULL,
                    exception_type TEXT,
                    traceback TEXT,
                    context TEXT
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level_time ON app_logs(level, created_at DESC)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_track_signatures (
                    signature TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rating INTEGER,
                    source TEXT NOT NULL,
                    promo_name TEXT,
                    reviewed_at REAL NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sig_time ON promo_track_signatures(reviewed_at)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS track_analysis (
                    file_hash TEXT PRIMARY KEY,
                    bpm REAL NOT NULL,
                    musical_key TEXT,
                    camelot_key TEXT,
                    drop_seconds REAL,
                    jump_delta REAL NOT NULL,
                    confidence REAL,
                    analyzed_at REAL NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_time ON track_analysis(analyzed_at)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS offline_promo_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    promo_key TEXT NOT NULL,
                    release_id INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    rating INTEGER,
                    comment TEXT,
                    favourite_track_id INTEGER,
                    promo_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    media_paths_json TEXT NOT NULL,
                    queued_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error_message TEXT
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_offline_status ON offline_promo_reviews(status)")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rating INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_comments_rating ON promo_comments(rating)")
            self._conn.commit()

    def _migrate_legacy_json_files(self) -> None:
        """One-time migration of loose ``inflyte_cache/release_<id>.json`` files into ``bakkis.db``."""
        cache_folder = self.db_path.parent / "inflyte_cache"
        if not cache_folder.is_dir():
            return
        for json_file in list(cache_folder.glob("release_*.json")):
            stem = json_file.stem
            rid_str = stem.replace("release_", "").strip()
            if not rid_str.isdigit():
                continue
            try:
                import json

                raw = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("tracks"):
                    self.put_promo_cache("inflyte", rid_str, raw)
                json_file.unlink()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


_DEFAULT_STORAGE: Optional[EncryptedStorage] = None
_STORAGE_LOCK = threading.Lock()


def get_storage() -> EncryptedStorage:
    global _DEFAULT_STORAGE
    with _STORAGE_LOCK:
        target_path = config_dir() / DB_FILENAME
        if _DEFAULT_STORAGE is None or _DEFAULT_STORAGE.db_path != target_path:
            if _DEFAULT_STORAGE is not None:
                _DEFAULT_STORAGE.close()
            _DEFAULT_STORAGE = EncryptedStorage(db_path=target_path)
        return _DEFAULT_STORAGE


__all__ = [
    "Aes256Cipher",
    "AnalysisStorageMixin",
    "CIPHER_PREFIX",
    "CryptoError",
    "DB_FILENAME",
    "DiagnosticStorageMixin",
    "EncryptedStorage",
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_SERVICE",
    "LibraryStorageMixin",
    "ReviewStorageMixin",
    "cleanup_media_files",
    "get_or_create_master_key",
    "get_storage",
]
