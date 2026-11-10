# PLN-05: Session Modularization, $O(1)$ Queue Optimization & Test Suite Segmentation

- **Feature ID**: PLN-05
- **Status**: Completed
- **Scope**: `bakkis_sorter/session/`, `bakkis_sorter/session.py`, `tests/session/`
- **Target Test Markers**: `pytest -m session -v` & `pytest tests/session/ -v`
- **Target Execution Speed**: < 0.50s for complete session suite; < 0.10s for isolated units
- **Verified**: 258/258 tests passing in 15.72s (`pytest -m session`: 23 passed in 0.26s; `pytest tests/session/`: 7 passed in 0.21s)
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Motivation
With `storage.py`, `api.py`, and `app.js` successfully modularized into domain mixins and ES modules, `session.py` (759 lines) remains the largest single monolith in the backend. It tightly couples 5 distinct subsystems:
1. Data models (`Track`, `Action`, `SessionError`)
2. Background I/O execution (`Worker` thread, file movement primitives)
3. Audit logging (`Journal` daily JSONL writer)
4. Copy-mode deduplication indexing (`load_copied_index`, `filter_already_copied`)
5. The core state machine (`Session`: sorting, undo, queue pagination, filtering, stats)

Furthermore, `session.py` contains two runtime performance bottlenecks on large libraries (5,000+ tracks):
- **$O(N)$ swipe penalty**: Swiping a card runs `self.queue.pop(0)`, which shifts 5,000+ memory pointers on every single swipe.
- **Search token recomputation**: `queue_slice(q="...")` recalculates `library.relative_posix(t.path, self.music_root).lower()` for every track in the library on every search keystroke.

In addition, `tests/test_session.py` (390 lines) runs all tests sequentially, preventing sub-second developer feedback loops on specific domains (e.g. testing queue filtering independently of worker thread disk I/O).

---

## 2. Target Architecture

```text
bakkis_sorter/
├── session/
│   ├── models.py                # Track, Action, SessionError, new_track (~65 lines)
│   ├── worker.py                # Worker thread, _move, _copy primitives (~75 lines)
│   ├── journal.py               # Journal class & audit logging (~55 lines)
│   ├── copy_index.py            # Copy-mode source tracking & filter_already_copied (~75 lines)
│   ├── queue.py                 # SessionQueueMixin: O(1) queue, search caching, filters (~180 lines)
│   ├── actions.py               # SessionActionsMixin: sort, skip, undo, undo_all (~190 lines)
│   └── __init__.py              # Composed Session class, snapshot, stats (~140 lines)
│
└── session.py                   # 25-line backwards-compatibility re-export facade

tests/
├── session/
│   ├── test_session_queue.py    # Queue slice, search caching, O(1) cursor (~0.08s)
│   ├── test_session_actions.py  # Sort, skip, undo, slot routing (~0.25s)
│   ├── test_session_worker.py   # FIFO queue, worker thread, error handling (~0.15s)
│   └── test_session_copy.py     # Deduplication index, copy mode filtering (~0.10s)
│
└── test_session.py              # Full backwards-compatibility regression runner
```

---

## 3. Detailed Component Allocation

### 3.1 `bakkis_sorter/session/models.py`
- Classes:
  - `SessionError(Exception)`
  - `@dataclass class Track`: `id: str`, `path: Path`, `name: str`, `rel_posix_lower: Optional[str] = None`
  - `@dataclass class Action`: full metadata record of a sort/skip action
- Functions:
  - `new_track(path: Path) -> Track` (generates incremental ID and caches lower-cased filename/path token)

### 3.2 `bakkis_sorter/session/worker.py`
- Primitives:
  - `_move(src: Path, dest: Path) -> None`
  - `_copy(src: Path, dest: Path) -> None`
  - `_is_empty(path: Path) -> bool`
- Class `Worker`:
  - FIFO daemon worker thread with `submit(job)`, `flush(timeout)`, and `stop()`
  - Isolated error handling callback

### 3.3 `bakkis_sorter/session/journal.py`
- Class `Journal`:
  - Append-only audit logger writing to `bakkis.db` and `<config>/journal/YYYY-MM-DD.jsonl`
  - Fully decoupled from `Session` state

### 3.4 `bakkis_sorter/session/copy_index.py`
- Path resolution: `copied_index_path(music_root: Path) -> Path`
- Storage & JSON synchronization:
  - `load_copied_index(music_root: Path, *, storage: Optional[Any] = None) -> dict`
  - `save_copied_index(music_root: Path, index: dict, *, storage: Optional[Any] = None) -> None`
  - `filter_already_copied(paths: list[Path], music_root: Path, index: Optional[dict] = None) -> list[Path]`

### 3.5 `bakkis_sorter/session/queue.py` (`SessionQueueMixin`)
- State management:
  - `self.tracks: dict[str, Track]`
  - `self.queue: collections.deque[Track]` (or `list[Track]` with $O(1)$ pop/advance operations)
  - `self.held: list[Track]`
  - `self.filter: Optional[dict]`
- Methods:
  - `append_tracks(paths: list[Path]) -> list[Track]`
  - `add_next(path: Path) -> Track`
  - `set_order(order: str) -> None`
  - `play_next(track_id: str) -> None`
  - `filter_pool() -> list[Track]`
  - `apply_filter(keep: set, order: Optional[list] = None, info: Optional[dict] = None) -> None`
  - `clear_filter() -> None`
  - `queue_slice(offset: int = 0, limit: int = 100, q: str = "") -> dict`:
    - Uses pre-computed `rel_posix_lower` on `Track` to filter in **sub-millisecond time** without re-resolving paths

### 3.6 `bakkis_sorter/session/actions.py` (`SessionActionsMixin`)
- Methods:
  - `sort(track_id: str, mode: str, slot: int) -> Action`
  - `skip(track_id: str, mode: str) -> Action`
  - `_start_action(...) -> Action`
  - `_run_action(...) -> None` (worker thread callback)
  - `undo_last() -> Optional[Action]`
  - `undo_all() -> list[Action]`
  - `_run_undo(...) -> None` (worker thread callback)

### 3.7 `bakkis_sorter/session/__init__.py` (`Session`)
- Composed class:
  ```python
  class Session(SessionQueueMixin, SessionActionsMixin):
      def __init__(self, music_root: Path, paths: list[Path], ...): ...
  ```
- Methods:
  - `snapshot() -> dict`
  - `folder_count(preset: dict, slot: int) -> int`
  - `active_dests() -> frozenset`
  - `notify(level: str, message: str, **extra: object) -> None`
  - `notices_since(seq: int) -> list[dict]`
  - `flush() -> None`
  - `close(timeout: Optional[float] = None) -> bool`
- Re-exports:
  - `Action`, `Journal`, `Session`, `SessionError`, `Track`, `Worker`, `copied_index_path`, `filter_already_copied`, `load_copied_index`, `new_track`, `save_copied_index`

---

## 4. Modular Test Suite Specification (`tests/session/`)

| Test File | Lines | Scenarios Covered | Target Time |
| :--- | :--- | :--- | :--- |
| **`test_session_queue.py`** | ~90 | Queue pagination, substring search with cached tokens, `append_tracks`, `add_next`, filter pool, harmonic filters | **~0.08s** |
| **`test_session_actions.py`**| ~120 | Sort slot routing, skip folder structure, undo single, undo all, name collision resolution | **~0.25s** |
| **`test_session_worker.py`** | ~70 | Worker FIFO execution order, flush timeout, error recovery callback, stop | **~0.15s** |
| **`test_session_copy.py`** | ~60 | Copy index persistence in SQLite KV store, file size comparison, unlinking on complete undo | **~0.10s** |

---

## 5. Implementation & Verification Sequence

1. **Step 1: Create `bakkis_sorter/session/` package**:
   - Write `models.py`, `worker.py`, `journal.py`, `copy_index.py`.
   - Write `queue.py` with fast cached search token lookup.
   - Write `actions.py` and `__init__.py`.
2. **Step 2: Convert `bakkis_sorter/session.py` to facade shim**:
   - Re-export all symbols from `bakkis_sorter.session`.
3. **Step 3: Create `tests/session/` modular test suite**:
   - Segment tests into domain modules.
4. **Step 4: Targeted Verification**:
   - Run `pytest tests/session/ -v` (**< 0.5s**).
   - Run `pytest -m session -v` (**< 0.5s**).
   - Run `pytest -m "not slow" -q` (**< 5.5s**).
   - Run full regression suite `pytest -q` (**251 passed in < 15s**).
5. **Step 5: Documentation & Git Commit**:
   - Update `docs/plans/index.md` status to `Completed`.
   - Commit locally to `feat/unified-promo-reviewer`.
