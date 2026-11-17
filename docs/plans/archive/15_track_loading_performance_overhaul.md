# PLN-15: Track Loading & Prefetch Performance Overhaul (v3.0.3)

## Summary
Eliminates multi-second track-loading stalls across Promo Review and Local Folder Sorting discovered via live profiling of v3.0.2.

## Root Causes & Fixes
1. **Non-Blocking `<2ms` Metadata (`metadata/reader.py`, `metadata/cache.py`, `backend/settings.py`)**: Removed synchronous `analyze_audio_file()` (`~5.57s` C++ DSP) from `read_meta()`, leaving fast Mutagen tag reading and SQLite `get_analysis()` lookups. Added `MetadataCache.invalidate()` and `update_analysis()`.
2. **Split Lookahead Warmup (`pipeline.py`)**: Separated `WarmupEngine` into a Fast I/O pool (3 workers for tags, covers, and PCM peaks) and a single `nice +10` DSP worker fed by a generation-prioritized `PriorityQueue` that skips tracks evicted from `_current_window`.
3. **Priority Promo Prefetch (`promos/session/core.py`, `stash.py`)**: Replaced FIFO `_prefetch_pool` with a tiered `PriorityQueue` giving `current` track uncontended network priority, dropping dismissed/rated promos immediately, removing `read_meta()` DSP from download workers, offloading optional promo DSP to a separate 1-worker `_dsp_pool` limited to `promos[:5]`, and preferring thumbnail URLs for `112px` card covers.
4. **Non-Blocking Async WKWebView Fetch & Parallel Boot (`bridge.py`, `promos/inflyte/bridge.py`, `promos/trackstack/client.py`, `backend/promos.py`)**: Added `_eval_async_js()` via `WKWebView.callAsyncJavaScript` + `fetch()` (preserving `_eval_js` fallback for unit tests) and loaded Trackstack + Inflyte concurrently.
5. **Frontend Localhost Socket Gate (`web/app.js`, `web/player.js`)**: Added `Player.cancelStalePreloads()` and deferred `Player.preload`, `Player.preloadAlt`, and upcoming cover/meta requests until `playback:playing` fires (with a `600ms` paused fallback).
