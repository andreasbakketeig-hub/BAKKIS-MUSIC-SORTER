# PLN-02: Virtual Scrolling Queue UI & Element Recycling Specification

- **Feature ID**: PLN-02
- **Status**: Completed
- **Scope**: `bakkis_sorter/web/queue.js`, `bakkis_sorter/web/panels.js`, `bakkis_sorter/web/app.js`, `bakkis_sorter/web/index.html`
- **Target Test Markers**: `pytest -m frontend -v`
- **Target Execution Speed**: < 0.20s
- **Verified**: 258/258 tests passing in 14.68s (`pytest -m frontend`: 4 passed in 0.23s)
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Motivation
Currently, when a user opens the Queue Drawer (<kbd>Q</kbd>) or scrolls through upcoming tracks:
1. **DOM Thrashing**: On every single scroll event (even a 1-pixel trackpad nudge), `QueueDrawer.renderList()` executes `inner.textContent = ""`. It destroys all 25–35 row elements and allocates new DOM nodes, event listeners, and CSS classes from scratch.
2. **Scroll Lock & Jitter**: During fast trackpad flicks or on 120Hz Apple ProMotion displays, firing 5+ teardowns and re-allocations in a 16ms window causes dropped frames, micro-stutters, and excessive CPU usage in `WKWebView`.
3. **Architectural Isolation**: `QueueDrawer` and `FilterPanel` are trapped inside `panels.js` (an un-modularized 1,145-line legacy script) instead of being a modular ES module like `cards.js`, `player.js`, or `stash.js`.

---

## 2. Technical Objectives

1. **Extract `bakkis_sorter/web/queue.js` as an ES Module**:
   - Extract `QueueDrawer` and `FilterPanel` into `bakkis_sorter/web/queue.js`.
   - Import `api`, `el`, `fmtTime`, `S`, `bus`, `toast`, and `queueChange`.
   - Export `initQueue()`, `QueueDrawer`, and `FilterPanel`.
   - Attach to `window.QueueDrawer` and `window.FilterPanel` for backwards compatibility with non-module panels.
2. **Implement DOM Element Recycling Pool**:
   - Maintain `renderedRows = new Map<number, HTMLElement>()` and `recycledPool = []`.
   - On scroll, compute `visibleRange = [first, last]`:
     - Rows no longer in `visibleRange` are removed from the DOM and added to `recycledPool`.
     - New rows in `visibleRange` borrow from `recycledPool` (or allocate once if pool is empty), update content/positions, and attach to DOM.
     - Rows already in `visibleRange` remain **completely untouched** (0 DOM mutations).
3. **`requestAnimationFrame` Render Throttling**:
   - When a scroll event occurs, set `pendingRaf = true` and schedule rendering via `requestAnimationFrame`.
   - Coalesces rapid multi-pixel events into exactly 1 render per display refresh cycle (solid 60/120fps).
4. **Reactive EventBus Integration**:
   - Subscribe `QueueDrawer` to `bus.on("session:changed")` and `bus.on("queue:refresh")` for automatic sync.

---

## 3. Data Flow & Recycling Architecture

```text
User scrolls queue (trackpad / mousewheel)
                    │
                    ▼
          [ onScroll Listener ]
                    │
                    ▼
     [ requestAnimationFrame Coalescing ]
  (Executes at most once per display frame)
                    │
                    ▼
   [ Calculate visible [first, last] indices ]
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
[ Rows leaving viewport ]   [ Rows entering viewport ]
         │                             │
   Remove from DOM             Borrow from recycledPool
         │                             │
Push to recycledPool           Update transform, text, index
                                       │
                               Attach to #queue-inner
```

---

## 4. Detailed Component Changes

### 4.1 New File: `bakkis_sorter/web/queue.js` (~220 lines)
- **Constants**: `QUEUE_ROW_H = 40`, `QUEUE_PAGE = 100`, `BUFFER_ROWS = 5`.
- **State**:
  - `query: ""`
  - `pages: Map<number, Track[]>`
  - `total: 0`
  - `renderedRows: Map<number, HTMLElement>`
  - `recycledPool: HTMLElement[]`
  - `rafId: 0`
- **Methods**:
  - `initQueue()`: wires search input, filter controls, and scroll listener with rAF.
  - `fetchPage(pageIndex)`: fetches tracks from `POST /api/queue` with cancellation check.
  - `renderList()`: element recycling algorithm with `style.transform = translateY(N px)`.
  - `renderHistory()`: optimistic recent actions list.
  - `playNext(track)`: calls `play_next` API and notifies via `toast()`.
  - `FilterPanel`: Camelot key select, BPM inputs, harmonic compatibility toggles.

### 4.2 `bakkis_sorter/web/panels.js`
- Remove `QueueDrawer` (lines 100–193) and `FilterPanel` (lines 196–267).
- Update `Drawers.toggle("queue")` and `Drawers.refreshSoon()` to invoke `QueueDrawer` via window or direct export.

### 4.3 `bakkis_sorter/web/app.js`
- Import `{ initQueue, QueueDrawer } from "./queue.js"`.
- Initialize in boot sequence: `initQueue()`.

### 4.4 `tests/test_web_modules.py`
- Add unit tests verifying `queue.js` module existence, ES export signatures, and MIME type serving.

---

## 5. Verification Plan

1. **Frontend Tests**:
   - Run `pytest -m frontend -v` to ensure module imports, syntax, and MIME types resolve correctly.
2. **Speed Run**:
   - `pytest -m "not slow"` (**< 5.5s**).
3. **Interactive Smoothness Check**:
   - Verify fast trackpad scrolling in Queue Drawer: 0 dropped frames, 0 DOM memory leak.
4. **Search Reactivity**:
   - Verify typing in search box filters smoothly with debounce.
