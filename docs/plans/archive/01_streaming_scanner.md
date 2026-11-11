# PLN-01: Streaming Folder Scanner Specification

- **Feature ID**: PLN-01
- **Status**: Completed
- **Scope**: `bakkis_sorter/library.py`, `bakkis_sorter/backend/library.py`, `bakkis_sorter/session.py`
- **Target Test Markers**: `pytest -m session -v` & `pytest -m "not slow" -k "scan or library"`
- **Target Execution Speed**: < 1.0s
- **Verified**: 251/251 tests passing in 13.80s (`pytest -m session`: 23 passed in 0.43s)

---

## 1. Problem Statement & Motivation
Currently, opening a music folder with 2,000–10,000 tracks requires `ScanJob` to walk the entire directory tree and collect all paths into a single list before initializing `Session`. The user waits 2–5 seconds watching a "Scanning…" spinner before the first track card appears.

With PLN-01, `bakkis_sorter` streams the first batch of 25 tracks to initialize the session and starts playback on Card 1 in **<50ms**, while remaining tracks stream asynchronously in the background.

---

## 2. Architecture & Data Flow

```text
User selects folder
         │
         ▼
[ api_load_folder(path) ]
         │
         ├──► ScanJob.start(on_batch=..., on_done=...)
                   │
                   ▼  (Walking filesystem via iter_audio_files)
       [ Batch 1 (First 25 tracks) ] ──► Initial Session(root, Batch 1)
                                         UI displays Card 1 (<50ms)
                                         Status: "ready"
                                         Audio playback starts
                   │
                   ▼
       [ Batch 2 (Next 25 tracks) ]  ──► session.append_tracks(Batch 2)
                   │
                   ▼
       [ Batch N (Remaining) ]       ──► session.append_tracks(Batch N)
                   │
                   ▼
       [ on_done(all_paths) ]        ──► Final scan count update, scan job closed
```

---

## 3. Detailed Component Changes

### 3.1 `bakkis_sorter/library.py`
1. Update `scan()`:
   - Accept `on_batch: Optional[Callable[[list[Path]], None]] = None` and `batch_size: int = 25`.
   - Maintain a buffer of paths. When buffer reaches `batch_size`, invoke `on_batch(list(buffer))` and clear buffer.
   - If stream finishes and buffer has remainder paths, invoke `on_batch(list(buffer))`.
2. Update `ScanJob`:
   - Accept `on_batch: Optional[Callable[[list[Path]], None]] = None` and `batch_size: int = 25`.
   - Forward `on_batch` callback to `scan()`.
   - Maintain full backwards compatibility for `on_done(paths)` and `job.result`.

### 3.2 `bakkis_sorter/session.py`
1. Add `Session.append_tracks(self, paths: list[Path]) -> list[Track]`:
   - Acquire `with self.lock:`.
   - Deduplicate paths against existing `self.tracks.values()`.
   - Instantiate new `Track` objects via `self._new_track(p)`.
   - Append to `self.queue`.
   - Increment `self.total`.
   - Trigger `self._changed()`.

### 3.3 `bakkis_sorter/backend/library.py`
1. Add `_scan_batch(self, job: library.ScanJob, root: Path, batch: list[Path]) -> None`:
   - If `self.session is None`:
     - Filter already copied if in copy mode.
     - Order tracks according to `track_order`.
     - Initialize `self.session = Session(root, ordered, ...)`.
     - Start `DuplicateIndex(root, ...)`.
     - Update `self.scan.update(status="ready", count=len(ordered))`.
     - Trigger `self._prefetch()` and `self._changed()`.
   - Else (session already active):
     - Filter already copied if in copy mode.
     - Order / shuffle batch.
     - Call `self.session.append_tracks(ordered)`.
     - Update `self.scan["count"] = self.session.total`.
     - Trigger `self._changed()`.
2. In `_scan_done`:
   - If session was not initialized (empty folder), initialize with empty list.
   - Set `self._scan_job = None`.
   - Update `self.scan["count"] = self.session.total`.
   - Trigger `self._changed()`.

---

## 4. Edge Cases & Safeguards

1. **Empty Folder**:
   - `on_batch` never fires. `_scan_done` fires with empty list, cleanly initializing `Session(root, [])` with count 0.
2. **Small Folders (< 25 tracks)**:
   - The remainder batch fires when `iter_audio_files` finishes, immediately starting the session.
3. **Cancellation (User loads another folder mid-scan)**:
   - `job.cancel()` sets `threading.Event()`. No callbacks fire after cancellation. Old session is safely closed.
4. **Copy Mode (`hide_copied_on_rescan`)**:
   - Every incoming batch is filtered against `filter_already_copied()` before being passed to `Session`.
5. **Thread Safety**:
   - All mutations of `Session.queue` and `AppBackend.session` are guarded by `self.lock` (`RLock`).

---

## 5. Verification Plan

1. **Unit Tests (`tests/test_library.py`)**:
   - Test `ScanJob` with `on_batch` callback receiving chunked path batches.
   - Verify cancellation mid-batch.
2. **Session Tests (`tests/test_session.py`)**:
   - Test `Session.append_tracks()` appending cleanly to an active queue without affecting `current` track.
3. **Integration Test (`tests/test_server.py`)**:
   - Test `load_folder` API returns initial chunk and streams full count.
4. **Targeted Speed Run**:
   - `.venv/bin/python -m pytest -m "not slow" -k "scan or library or session"` (**< 1.5s**).
   - Full suite `.venv/bin/python -m pytest -q` (**100% pass**).
