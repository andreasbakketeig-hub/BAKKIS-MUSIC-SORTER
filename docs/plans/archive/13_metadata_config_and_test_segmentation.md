# PLN-13: Metadata, Config & Test Suite Segmentation (Phase 3)

- **Feature ID**: PLN-13
- **Version Bump**: `2.1.4` -> `2.1.5`
- **Status**: Completed (Verified - 282 tests passing)
- **Scope**:
  - `bakkis_sorter/metadata.py` (688 lines) ➔ `bakkis_sorter/metadata/`:
    - `tags.py`: `TrackMeta`, `parse_filename`, `split_artist_title`, `clean_text`, `format_bpm`, `_tag`, `_scalar`
    - `reader.py`: `read_meta`, `file_digest`, `_open`
    - `cover.py`: `read_cover`, `downsample_cover`, `image_mime`, `_cover_from_audio`, `_pick`
    - `cache.py`: `ByteBoundedLRU`, `MetadataCache`, `content_digest`
    - `peaks.py`: `PeakStore` SQLite waveform analysis
    - `__init__.py`: Package entry re-exporting all symbols for 100% backward compatibility
  - `bakkis_sorter/config.py` (732 lines) ➔ `bakkis_sorter/config/`:
    - `paths.py`: `bundle_dir`, `config_dir`, `write_json_atomic`, `migrate_from_v1`
    - `coercions.py`: `coerce_bool`, `normalize_hex`, `coerce_palette_hex`, `safe_segment`, `norm_mode`
    - `settings.py`: `default_settings`, `normalize_settings`, `reset_settings`, `load_settings`, `save_settings`, `repair_default_folder`, `patch_settings`
    - `presets.py`: `default_swipe_preset`, `default_grid_preset`, `coerce_preset`, `normalize_preset_store`, `add_builtin_presets`, `PresetStore`, `PresetError`
    - `__init__.py`: Package entry re-exporting all symbols for 100% backward compatibility
  - `tests/test_server.py` (355 lines) ➔ modular test files:
    - `tests/server_helpers.py`: Shared server test fixtures and HTTP utilities (`app`, `music`, `api`, `load`)
    - `tests/test_server_core.py`: Static file serving, traversal defense, host header validation, HTTP errors, keep-alive resilience
    - `tests/test_server_media.py`: Audio streaming, byte ranges, cover extraction, ENOBUFS retry logic
    - `tests/test_server_api.py`: Sort/undo operations, queue paging, BPM/key filtering, promo history API, diagnostics & vacuum
  - Version Bump:
    - `bakkis_sorter/__init__.py`: `__version__ = "2.1.5"`
    - `PLAN.md`: Add row for v2.1.5
    - `tests/test_web_modules.py`: Update assertion to `__version__ == "2.1.5"`
- **Target Test Markers**: `pytest tests/test_metadata.py tests/test_config.py tests/test_server_*.py` (< 2.0s)
- **Target Execution Speed**: Full regression < 20s
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement

Following the completion of Phase 1 (`promos/session` and `storage/reviews`) and Phase 2 (`web/panels.js` and `web/app.css`), the remaining large files in the codebase are:
1. **`bakkis_sorter/metadata.py` (688 lines)**:
   A single file bundling tag parsing, Mutagen file opening, cover downsampling via PIL, byte-bounded in-memory LRU caching, and waveform peak persistence in SQLite.
2. **`bakkis_sorter/config.py` (732 lines)**:
   A single file bundling filesystem path resolution, JSON migrations from v1, settings validation/sanitization, preset manipulation, and preset persistence.
3. **`tests/test_server.py` (355 lines)**:
   A monolithic HTTP integration test file mixing security checks, media streaming, queue navigation, and promo database queries.

---

## 2. Root-Cause Lessons from Phase 2 Applied to Phase 3

To prevent the latency and token overhead experienced in earlier phases, Phase 3 adopts the following operational rules:

1. **Zero Conversational Paging**:
   No reading files in 100-line conversational chunks. All symbol boundaries are analyzed upfront via Python's `ast` module.
2. **Atomic One-Shot Generation**:
   Offload package creation to an atomic script that segments modules in a single step instead of dozens of sequential tool calls.
3. **100% Backward-Compatibility Facades (`__init__.py`)**:
   By retaining full symbol re-exports in `metadata/__init__.py` and `config/__init__.py`, zero import paths in the rest of the application or tests break (`from bakkis_sorter import metadata`, `from bakkis_sorter.config import PresetStore`, etc. continue to work without modification).
4. **Pre-Flight Test Verification**:
   Targeted test verification runs in < 2.0s before proceeding to the full regression test suite.

---

## 3. Architecture & Modular Boundary Design

### 3.1 Metadata Modularization (`bakkis_sorter/metadata/`)

```
bakkis_sorter/metadata/
├── __init__.py    # Re-exports all public symbols & functions
├── tags.py        # TrackMeta, parse_filename, split_artist_title, clean_text, format_bpm, _tag, _scalar
├── reader.py      # read_meta, file_digest, _open
├── cover.py       # read_cover, downsample_cover, image_mime, _cover_from_audio, _pick
├── cache.py       # ByteBoundedLRU, _LRU, MetadataCache, content_digest
└── peaks.py       # PeakStore (waveform analysis BLOB cache)
```

- **`tags.py`**: Pure metadata structures, string sanitizers, site stamp removers, and filename parsers. Zero I/O dependencies.
- **`reader.py`**: Mutagen file opener and tag extractor. Depends on `tags.py`.
- **`cover.py`**: Embedded artwork extraction and Pillow/PIL thumbnail downsampler (320px).
- **`cache.py`**: High-performance multi-threaded background metadata prefetching and byte-bounded memory LRU.
- **`peaks.py`**: Waveform peak extraction and SQLite storage.

---

### 3.2 Configuration Modularization (`bakkis_sorter/config/`)

```
bakkis_sorter/config/
├── __init__.py    # Re-exports all public symbols & functions
├── paths.py       # bundle_dir, config_dir, write_json_atomic, migrate_from_v1
├── coercions.py   # coerce_bool, normalize_hex, coerce_palette_hex, safe_segment, norm_mode, MODES
├── settings.py    # default_settings, normalize_settings, reset_settings, load_settings, save_settings, repair_default_folder, patch_settings
└── presets.py     # default_swipe_preset, default_grid_preset, coerce_preset, normalize_preset_store, add_builtin_presets, PresetStore, PresetError
```

- **`paths.py`**: OS file locations, macOS Application Support directories, and atomic JSON writers.
- **`coercions.py`**: Primitive data sanitizers, color palette validators, safe filesystem segment formatters.
- **`settings.py`**: Settings loading, saving, normalization, and account repair.
- **`presets.py`**: Preset CRUD, default genre maps, validation, and JSON storage.

---

### 3.3 Server Test Suite Modularization (`tests/`)

```
tests/
├── server_helpers.py       # app fixture, music fixture, load(), api() helpers
├── test_server_core.py     # Static files, security, error handling, keep-alive
├── test_server_media.py    # Audio ranges, cover art, ENOBUFS retry
└── test_server_api.py      # Sort/undo, queue paging, bpm/key filter, promo history, diagnostics
```

---

## 4. Execution Pipeline (4 Atomic Steps)

1. **Step 1: Atomic Generation Script**:
   Execute a Python script to create `bakkis_sorter/metadata/` and `bakkis_sorter/config/` with clean imports, and remove monolithic `metadata.py` and `config.py`.
2. **Step 2: Server Tests Split**:
   Create `tests/server_helpers.py`, `tests/test_server_core.py`, `tests/test_server_media.py`, `tests/test_server_api.py`, and remove monolithic `tests/test_server.py`.
3. **Step 3: Version Bump to `2.1.5`**:
   Update `bakkis_sorter/__init__.py`, `PLAN.md`, and `tests/test_web_modules.py`.
4. **Step 4: Verification & Git Commit**:
   - Run targeted tests: `pytest tests/test_metadata.py tests/test_config.py tests/test_server_*.py` (< 2.0s).
   - Run full regression: `pytest -q` (all 282+ tests pass).
   - Commit locally on `feat/unified-promo-reviewer`.

---

## 5. Backward Compatibility Guarantee

Because `bakkis_sorter/metadata/__init__.py` and `bakkis_sorter/config/__init__.py` re-export all symbols in `__all__`, any code or external test importing:
```python
from bakkis_sorter import metadata
from bakkis_sorter.metadata import read_meta, MetadataCache
from bakkis_sorter.config import PresetStore, default_settings
```
will resolve immediately with **zero breaking changes**.
