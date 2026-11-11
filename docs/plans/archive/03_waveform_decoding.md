# PLN-03: Waveform Range & BLOB Storage Engine Specification

- **Feature ID**: PLN-03
- **Status**: Ready
- **Scope**: `bakkis_sorter/waveform.py`, `bakkis_sorter/storage/analysis.py`, `bakkis_sorter/storage/__init__.py`, `bakkis_sorter/metadata.py`, `bakkis_sorter/backend/settings.py`, `bakkis_sorter/web/wave.js`
- **Target Test Markers**: `pytest -m audio -v` & `pytest -m storage -v`
- **Target Execution Speed**: < 0.80s
- **Storage Format**: Native binary `BLOB` (800 bytes per record) in SQLite WAL mode
- **Max Cache Capacity**: Adjustable, default **100,000 entries** (~86 MB peak database footprint)
- **Cache Invalidation / Identity**: Path-independent audio content hash (file size + head/tail 16KB signature)
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Motivation

Currently, waveform peaks are computed and stored with multiple architectural inefficiencies:
1. **WebKit Memory Inflation**: For compressed audio (MP3, AAC, FLAC), the frontend fetches the entire audio file into WebKit and calls `OfflineAudioContext.decodeAudioData()`, allocating 60–120 MB of uncompressed float buffers in browser RAM per song.
2. **Filesystem Fragmentation**: WAV/AIFF peaks were historically saved as thousands of loose `.json` files inside `~/.bakkis/peaks/` on disk, causing APFS block slack waste (4KB minimum per file) and OS directory handle churn.
3. **SQLite Page Overflow with Base64**: Peaks are 800 binary bytes (`uint8` values 0–255). When encoded as Base64 text (1,068 bytes), rows exceed SQLite's 4KB in-page threshold (~1,018 bytes), forcing SQLite to allocate external overflow pages and almost doubling the database size.
4. **Cache Invalidation on Track Move (Sorting)**: The legacy cache key incorporates `str(path)`. When a track is moved from a staging queue into a destination genre/quarter folder, the cache key changes, stranding an orphaned record in SQLite and forcing recalculation if the file is previewed again.

---

## 2. 1,000,000 Entries Scale, Latency & Capacity Analysis

### 2.1 Storage Footprint

| Storage Architecture | Payload / Entry | SQLite Page Fit (4KB Page) | Total Database Size | Slack / Overhead |
| :--- | :--- | :--- | :--- | :--- |
| **Loose Files (`~/.bakkis/peaks/*.json`)** | 1,120 bytes JSON | N/A (1 file per track) | **~4.10 GB** | **+300%** (APFS 4KB block slack) |
| **SQLite `TEXT` (Base64)** | 1,068 bytes string | Exceeds 1,018-byte limit → **spills to overflow pages** | **~1.75 GB** | **+65%** (Overflow page pointers) |
| **SQLite `BLOB` (Binary)** | **800 bytes uint8** | **Fits 100% in page (4 records/page, 0 overflow pages)** | **~1.06 GB** (at 1M) / **~86 MB** (at 100k cap) | **0%** (Optimal packed density) |

### 2.2 Latency Impact (Per Track Peak Lookup)

| Stage / Operation | Memory-Cached (Warm) | Cold Disk (Apple Silicon NVMe) | Impact / Note |
| :--- | :--- | :--- | :--- |
| **B-Tree Navigation** | ~0.008 ms (8 µs) | ~0.10 ms (100 µs) | Tree depth is only **4 levels** for 1M entries |
| **BLOB Deserialization** | ~0.0004 ms (0.4 µs) | ~0.0004 ms (0.4 µs) | Direct C buffer read, zero string parsing |
| **Optional Base64 encode (API)** | ~0.0008 ms (0.8 µs) | ~0.0008 ms (0.8 µs) | Native C stdlib `b64encode` |
| **Total Query Latency** | **~0.01 ms (10 µs)** | **~0.11 ms (110 µs)** | **Instantaneous** (> 1,500x faster than 60 FPS frame) |

### 2.3 RAM Isolation
- SQLite uses page-on-demand VFS: only the requested 4KB leaf page is read into the **2.0 MB** SQLite pager cache (`PRAGMA cache_size = -2000`).
- The full 1GB database is **never** loaded into RAM. Python heap delta per track lookup is < 1 KB.

---

## 3. Architecture & Key Features

### 3.1 Path-Independent Content Hash
Instead of including `str(path)`, compute a fast, path-independent signature from audio file attributes:
```python
def content_digest(path: Path) -> Optional[str]:
    """Fast, collision-resistant audio digest based on size + head/tail sample chunks (0.08ms)."""
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return None
        with open(path, "rb") as f:
            head = f.read(16384)
            tail = b""
            if st.st_size > 32768:
                f.seek(-16384, os.SEEK_END)
                tail = f.read(16384)
        hasher = hashlib.sha1()
        hasher.update(f"{st.st_size}:".encode("ascii"))
        hasher.update(head)
        if tail:
            hasher.update(tail)
        return hasher.hexdigest()
    except OSError:
        return None
```
- **Move-Resilient**: When tracks are sorted (moved from staging to destination), `size`, `head`, and `tail` remain identical. The cached waveform BLOB is instantly resolved at the new location without any DB updates.
- **Rename-Resilient**: Renaming a file preserves the audio digest.

### 3.2 Adjustable Database Cap & LRU Eviction
- **Capacity**: Default `max_waveform_entries = 100_000` (configurable via `Storage(max_waveform_entries=...)` or settings).
- **Index**: `CREATE INDEX IF NOT EXISTS idx_waveform_cached_at ON waveform_peaks (cached_at);`
- **Amortized Pruning**: Inserts track count periodically (every 1,000 inserts or during maintenance vacuum). If count exceeds `max_waveform_entries`, evicts oldest $N$ entries in one indexed sub-millisecond query:
  ```sql
  DELETE FROM waveform_peaks 
  WHERE file_hash IN (
      SELECT file_hash FROM waveform_peaks 
      ORDER BY cached_at ASC 
      LIMIT ?
  );
  ```

### 3.3 Native SQLite `BLOB` Storage & Migration
- Schema:
  ```sql
  CREATE TABLE IF NOT EXISTS waveform_peaks (
      file_hash TEXT PRIMARY KEY,
      peaks BLOB NOT NULL,
      cached_at REAL NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_waveform_cached_at ON waveform_peaks (cached_at);
  ```
- Transparent Fallback:
  - `get_peaks_bytes`: Supports both binary `BLOB` and legacy `TEXT` (Base64).
  - Legacy disk `.json` files are migrated to SQLite `BLOB` on cache hit and deleted from disk.

---

## 4. Component Modifications Detail

### 4.1 Storage Mixin: `bakkis_sorter/storage/analysis.py`
Add `get_peaks_bytes`, `put_peaks_bytes`, and `prune_peaks`:
```python
DEFAULT_MAX_PEAKS_ENTRIES = 100_000

class AnalysisStorageMixin:
    max_waveform_entries: int = DEFAULT_MAX_PEAKS_ENTRIES
    _peaks_insert_counter: int = 0

    def get_peaks_bytes(self, file_hash: str) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute(
                "SELECT peaks FROM waveform_peaks WHERE file_hash = ?",
                (str(file_hash),),
            ).fetchone()
        if not row or row[0] is None:
            return None
        val = row[0]
        if isinstance(val, (bytes, memoryview)):
            return bytes(val)
        if isinstance(val, str):
            try:
                return base64.b64decode(val)
            except Exception:
                return None
        return None

    def put_peaks_bytes(self, file_hash: str, peaks: bytes) -> None:
        now = time.time()
        blob = sqlite3.Binary(peaks)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO waveform_peaks (file_hash, peaks, cached_at)
                VALUES (?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    peaks = excluded.peaks,
                    cached_at = excluded.cached_at
                """,
                (str(file_hash), blob, now),
            )
            self._conn.commit()
            self._peaks_insert_counter += 1
            if self._peaks_insert_counter >= 1000:
                self._peaks_insert_counter = 0
                self._maybe_prune_peaks()

    def prune_peaks(self, max_entries: Optional[int] = None) -> int:
        limit = max_entries if max_entries is not None else self.max_waveform_entries
        with self._lock:
            count = self._conn.execute("SELECT count(*) FROM waveform_peaks").fetchone()[0]
            if count <= limit:
                return 0
            excess = (count - limit) + 1000
            cur = self._conn.execute(
                """
                DELETE FROM waveform_peaks 
                WHERE file_hash IN (
                    SELECT file_hash FROM waveform_peaks 
                    ORDER BY cached_at ASC 
                    LIMIT ?
                )
                """,
                (excess,),
            )
            pruned = cur.rowcount
            self._conn.commit()
            return pruned
```

### 4.2 Metadata & PeakStore: `bakkis_sorter/metadata.py`
Add `content_digest(path)` and dual resolution:
```python
def content_digest(path: Path) -> Optional[str]:
    """Fast, collision-resistant audio digest based on size + head/tail sample chunks."""
    try:
        st = os.stat(path)
        if st.st_size == 0:
            return None
        with open(path, "rb") as f:
            head = f.read(16384)
            tail = b""
            if st.st_size > 32768:
                f.seek(-16384, os.SEEK_END)
                tail = f.read(16384)
        hasher = hashlib.sha1()
        hasher.update(f"{st.st_size}:".encode("ascii"))
        hasher.update(head)
        if tail:
            hasher.update(tail)
        return hasher.hexdigest()
    except OSError:
        return None

class PeakStore:
    # get_bytes checks content_digest first, then legacy digest for migration.
    # put_bytes always stores under content_digest.
```

### 4.3 API Integration: `bakkis_sorter/backend/settings.py`
In `api_peaks_get`:
- Query `self.peaks.get_bytes(p)`.
- If missing, compute via `waveform.compute(p)`, store via `self.peaks.put_bytes(p, raw)`, and return Base64 string for JSON transport.

---

## 5. Verification & Testing Plan

1. **Storage Tests (`tests/test_storage.py`)**:
   - `test_waveform_peaks_blob_storage_and_backwards_compat`:
     - Test storing 800-byte raw `bytes` via `put_peaks_bytes`.
     - Test retrieval via `get_peaks_bytes` matches exact bytes.
     - Test retrieval via legacy `get_peaks` returns valid Base64 string.
     - Test reading legacy `TEXT` rows through `get_peaks_bytes`.
     - Test updating existing keys (`ON CONFLICT`).
   - `test_waveform_peaks_prune_lru_cap`:
     - Set `max_waveform_entries = 10`.
     - Insert 15 entries with staggered timestamps.
     - Verify oldest 5 entries are pruned and most recent 10 remain.
2. **Metadata Tests (`tests/test_metadata.py`)**:
   - `test_peak_store_content_digest_move_resilience`:
     - Create audio file at `path1`.
     - Put waveform into PeakStore.
     - Move file (`shutil.move(path1, path2)`).
     - Verify `store.get_bytes(path2)` immediately retrieves the cached waveform without recalculation!
3. **Execution Speed & Markers**:
   - `pytest -m "audio or storage" -v` in **< 0.80s**.
   - Full suite `pytest -q` (all 258+ tests passing).
