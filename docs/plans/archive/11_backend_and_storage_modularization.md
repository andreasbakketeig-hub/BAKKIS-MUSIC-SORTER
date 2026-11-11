# PLN-11: Backend & Storage Modularization (Phase 1)

- **Feature ID**: PLN-11
- **Status**: Completed (Verified - 282 tests passing)
- **Scope**:
  - `bakkis_sorter/promos/session.py` ➔ `bakkis_sorter/promos/session/`
    - `__init__.py`
    - `core.py`
    - `actions.py`
    - `stash.py`
    - `offline.py`
  - `bakkis_sorter/storage/reviews.py` ➔ segmented mixins:
    - `bakkis_sorter/storage/reviews.py`
    - `bakkis_sorter/storage/signatures.py`
    - `bakkis_sorter/storage/offline_queue.py`
    - `bakkis_sorter/storage/comments.py`
    - `bakkis_sorter/storage/__init__.py`
  - Tests:
    - `tests/test_storage.py`
    - `tests/test_inflyte.py`
    - `tests/test_trackstack.py`
    - `tests/test_comments_storage.py`
- **Target Test Markers**: `pytest -m "promos or storage" -v`
- **Target Execution Speed**: < 1.5s
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement

Two foundational backend files have exceeded maintainability thresholds:
1. **`bakkis_sorter/promos/session.py` (1,099 lines)**:
   A single file managing track traversal pointers, UI snapshots, decision routing, Inflyte API submissions, Trackstack downloads, multi-threaded audio/cover prefetching, flight stash queues, and offline synchronization.
2. **`bakkis_sorter/storage/reviews.py` (459 lines)**:
   A single mixin housing five unrelated database tables (`promo_reviews`, `promo_track_signatures`, `offline_promo_reviews`, `stashed_promos`, `promo_cache`, and `promo_comments`).

---

## 2. Architecture & Modular Boundary Design

### 2.1 Promo Session Modularization (`bakkis_sorter/promos/session/`)

```
bakkis_sorter/promos/session/
├── __init__.py      # Re-exports InflyteSession, InflyteTrack, InflytePromo, etc.
├── core.py          # Session init, queue traversal, track index, snapshot dicts
├── actions.py       # Action execution (sort, skip, undo, feedback dispatch)
├── stash.py         # Offline flight stash worker & media prefetch downloads
└── offline.py       # Offline review queue synchronization and re-submission
```

- **`core.py` (`SessionCoreMixin`)**:
  - Manages `self.promos`, `self.current_promo_index`, `self.current_track_index`.
  - Computes `snapshot()`, `total`, `remaining`, `current`.
  - Thread safety via `self.lock`.
- **`actions.py` (`SessionActionsMixin`)**:
  - `sort(track_id, mode, slot)`
  - `skip(track_id)`
  - `undo()`
  - `_execute_action(action)`: Dispatches Inflyte rating/comment or Trackstack download/support.
- **`stash.py` (`SessionStashMixin`)**:
  - `stash_promos(target_count)`
  - `_stash_worker()`
  - `_download_file(url, dest)`
  - `get_stash_state()`
- **`offline.py` (`SessionOfflineMixin`)**:
  - `sync_offline_reviews()`
  - `_sync_worker()`
- **`__init__.py`**:
  - `class InflyteSession(SessionCoreMixin, SessionActionsMixin, SessionStashMixin, SessionOfflineMixin): ...`
  - Maintains 100% backward compatibility for all callers:
    `from bakkis_sorter.promos.session import InflyteSession`.

---

### 2.2 Storage Mixin Segmentation (`bakkis_sorter/storage/`)

```
bakkis_sorter/storage/
├── __init__.py        # EncryptedStorage composing all mixins
├── crypto.py          # AES-256 GCM cipher & master key derivation
├── library.py         # Folder & scan caching
├── analysis.py        # Waveform peaks BLOB storage
├── diagnostics.py     # Vacuum & DB statistics
├── reviews.py         # promo_reviews CRUD, is_reviewed, review history
├── signatures.py      # promo_track_signatures duplicate detection & retention
├── offline_queue.py   # offline_promo_reviews, stashed_promos, promo_cache
└── comments.py        # promo_comments table & default comment migration
```

- **`storage/reviews.py` (`ReviewStorageMixin`)**:
  - `record_review(source, promo_key, decision, details, media_paths)`
  - `is_reviewed(source, promo_key)`
  - `get_reviewed_keys(source)`
  - `get_promo_review_history(...)`
- **`storage/signatures.py` (`SignatureStorageMixin`)**:
  - `record_track_signature(...)`
  - `get_track_signature_match(signature)`
  - `prune_track_signatures(retention_days)`
- **`storage/offline_queue.py` (`OfflineQueueStorageMixin`)**:
  - `queue_offline_review(...)`
  - `get_pending_offline_reviews()`
  - `mark_offline_review_synced(review_id)`
  - `mark_offline_review_failed(review_id, err)`
  - `get_all_stashed_promos()`
  - `get_promo_cache(source, promo_key)`
  - `put_promo_cache(source, promo_key, payload)`
  - `delete_promo_cache(source, promo_key)`
- **`storage/comments.py` (`CommentStorageMixin`)**:
  - `get_promo_comments()`
  - `set_promo_comments(pools)`
- **`storage/__init__.py`**:
  - `class EncryptedStorage(CryptoMixin, LibraryStorageMixin, AnalysisStorageMixin, DiagnosticsStorageMixin, ReviewStorageMixin, SignatureStorageMixin, OfflineQueueStorageMixin, CommentStorageMixin): ...`

---

## 3. Backward Compatibility & Verification

1. **Zero External Breaking Changes**:
   - `from bakkis_sorter.promos.session import InflyteSession` works unchanged.
   - `from bakkis_sorter.storage import EncryptedStorage` works unchanged.
   - `storage.record_review`, `storage.record_track_signature`, `storage.get_promo_comments` remain identical.
2. **Incremental Testing**:
   - Run `pytest tests/test_comments_storage.py tests/test_inflyte.py tests/test_trackstack.py tests/test_storage.py -v`.
   - Run full regression suite (`pytest -q` - all 282 tests must pass).
