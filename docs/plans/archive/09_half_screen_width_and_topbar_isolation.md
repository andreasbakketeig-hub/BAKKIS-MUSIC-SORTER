# PLN-09: Half-Screen Width Constraint, Topbar Session Isolation & Home Navigation

- **Feature ID**: PLN-09
- **Status**: Completed (Verified - 279 tests passing)
- **Scope**: 
  - `bakkis_sorter/web/index.html`
  - `bakkis_sorter/web/app.css`
  - `bakkis_sorter/web/app.js`
  - `bakkis_sorter/backend/library.py`
  - `tests/test_web_modules.py`
  - `tests/test_server.py`
- **Target Test Markers**: `pytest -m "frontend or server" -v`
- **Target Execution Speed**: < 0.60s
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Mathematical Diagnosis

In the user's half-screen screenshot (window width: **891px**):
1. **The Top Bar Blew Up Container Width to ~1440px**:
   - `header.topbar` contained 14 non-wrapping buttons side-by-side (`sparks`, `⚡ Review Promos`, `Inflyte`, `Trackstack`, `💬 Comments`, `✈️ Stash`, `• Sync (0)`, `Promos`, `Mode Switch`, etc.).
   - Regular local music sorting was displaying all 6 promo service buttons simultaneously.
   - Because `body` was `display: grid; grid-template-rows: auto 1fr auto;` with default `grid-template-columns: auto` (which evaluates to `minmax(auto, 1fr)`), CSS Grid expanded `body`, `main`, and the footer to match the topbar's unshrunk content width (**~1440px**).
2. **Artificial 321px Content Shift**:
   - `.screen-player` has padding: `padding: 16px max(16px, calc((100% - clamp(540px, 85vw, 1280px)) / 2));`.
   - In CSS, `100%` in padding resolves to the inline width of the containing block (`<main>`).
   - With `<main>` stretched to 1440px by the topbar, WebKit evaluated `100%` as 1440px, and `85vw` as 757px.
   - Left padding was calculated as `(1440px - 757px) / 2 = 321.5px`!
   - Content took `757px`.
   - Total width: `321.5px (left padding) + 757px (tiles) = 1078.5px`.
   - In an 891px window, everything past 891px (Tile 4, right half of waveform, top icons) was cut off!
3. **No Direct Way to Return to the Landing Screen**:
   - Once inside a sorting or promo session, there was no dedicated button to return to the Welcome Hub landing screen without restarting the app.

---

## 2. Technical Solution & Architecture

### 2.1 CSS Grid Strict Viewport Clamping (`app.css`)
Prevent any child element from expanding the grid container past the viewport width:
```css
html, body {
  margin: 0;
  height: 100%;
  max-width: 100vw;
  overflow-x: hidden;
}

body {
  display: grid;
  grid-template-columns: minmax(0, 1fr); /* Crucial: stops children from pushing track width */
  grid-template-rows: auto 1fr auto;
  width: 100%;
  max-width: 100vw;
  overflow-x: hidden;
}

.topbar, main, footer {
  min-width: 0;
  max-width: 100vw;
  overflow-x: hidden;
}
```
Setting `grid-template-columns: minmax(0, 1fr);` forces `<main>` to strictly match the actual window width (e.g. 891px). At 891px:
- `100%` = 891px
- `85vw` = 757px
- Left padding = `(891px - 757px) / 2 = 67px`
- Content width = `757px`
- Right padding = `67px`
- Total = `891px` (100% fits on screen, perfectly centered, zero cut off!).

---

### 2.2 Contextual Top Bar Visibility (`app.js` & `index.html`)
Isolate promo buttons so they only display when actively reviewing promos:
1. **Local Music Sort Session (`!isPromo`)**:
   - Hide promo-specific buttons: `#btn-top-inflyte`, `#btn-top-login-inflyte`, `#btn-top-login-trackstack`, `#btn-edit-comments`, `#btn-stash-flight`, `#btn-sync-offline`.
   - Only show local sorting controls: Home button, `#folder-chip`, `#preset-select`, `.mode-switch`, `#count`, `#op-badge`, `.top-icons`.
   - Natural topbar width drops from 1440px to ~480px!
2. **Promo Review Session (`isPromo`)**:
   - Hide local folder controls: `#folder-chip`, `#btn-top-inflyte`.
   - Show promo connection pills (`Inflyte`, `Trackstack`), `💬 Comments`, `✈️ Stash`, `Sync`.
   - Mark `body.is-promos` so CSS can optimize spacing.
3. **Empty Landing State (`is-empty`)**:
   - Hide all session chips in the top bar; the Welcome Hub presents the 2 primary cards cleanly.

---

### 2.3 Return to Landing Screen (Home Button & Navigation)

#### A. Backend Session Unloading API (`bakkis_sorter/backend/library.py`)
Add `api_close_session` to `LibraryApiMixin`:
```python
    def api_close_session(self) -> dict:
        """Close the active sorting or promo session and return to empty landing screen."""
        with self.lock:
            if self._scan_job is not None:
                self._scan_job.cancel()
                self._scan_job = None
            if self.session is not None:
                self.session.close()
                self.session = None
            self.scan = None
            self._changed()
        return {"ok": True}
```

#### B. Top Bar Home Button (`index.html`)
Place a clear, intuitive Home button in the top bar beside the logo:
```html
<button type="button" class="icon-btn" id="btn-home" title="Return to Welcome Hub (Home)" aria-label="Return to Welcome Hub">
  <svg viewBox="0 0 24 24"><path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H4a1 1 0 0 1-1-1V9.5z"/></svg>
</button>
```
Also make the `.brand-lockup` clickable as a home button.

#### C. Frontend Home Handler (`app.js`)
```javascript
export async function goHome() {
  Player.clear();
  try {
    await api("close_session");
  } catch (_) {}
  S.session = null;
  setScreen("empty");
  renderEmpty();
  toast("Returned to Welcome Hub", { ms: 1800 });
}
```
- Available in both:
  - **Local music sorting session**: Safely flushes background moves, closes the session, and presents the Welcome Hub.
  - **Promo review session**: Safely flushes reviews/downloads, stops audio, and presents the Welcome Hub.

---

### 2.4 Waveform & Decision Tiles Responsive Resilience
1. **Waveform Canvas**:
   - Ensure `#wave-canvas` and `.wave-block` obey `min-width: 0; max-width: 100%;`.
2. **Swipe Decision Tiles**:
   - At window width 720px–1100px: 4 tiles fit comfortably across the 85vw stage (~180px per tile).
   - At `@media (max-width: 700px)`: Switch to 2x2 grid (`repeat(2, 1fr)`).

---

## 3. Component Modifications Detail

| File | Changes |
| :--- | :--- |
| **`bakkis_sorter/backend/library.py`** | Add `api_close_session()` to cleanly close any active session. |
| **`bakkis_sorter/web/index.html`** | 1. Add `#btn-home` to top bar.<br>2. Set default `hidden` on promo topbar buttons.<br>3. Make `.brand-lockup` interactive. |
| **`bakkis_sorter/web/app.css`** | 1. Add `grid-template-columns: minmax(0, 1fr);` and `max-width: 100vw; overflow-x: hidden;` to `body`.<br>2. Add `min-width: 0; max-width: 100vw; overflow-x: hidden;` to `.topbar` and `main`.<br>3. Contextual CSS rules for `.is-player` vs `.is-promos`.<br>4. Style `#btn-home`. |
| **`bakkis_sorter/web/app.js`** | 1. Implement `goHome()` and wire `#btn-home` and `.brand-lockup`.<br>2. Update `renderTopbar()` to dynamically toggle promo buttons vs local sorting controls based on `isPromo`. |
| **`tests/test_web_modules.py`** | Unit tests asserting `minmax(0, 1fr)` grid constraint, home button markup, and topbar context rules. |
| **`tests/test_server.py`** | Unit test verifying `api_close_session` endpoint. |

---

## 4. Verification Plan

1. **Unit Tests**:
   - Run `pytest -m "frontend or server" -v` in **< 0.60s**.
   - Run full suite `pytest -q` (all 277+ tests pass).
2. **Visual & Layout Verification in Half-Screen (891px)**:
   - Verify `<main>` width matches 891px exactly.
   - Verify all 4 tiles (`← Not for me`, `↓ Skip`, `↑ Feeling it`, `→ Keep`) are completely visible and centered.
   - Verify Settings gear icon in top bar is completely visible at the right edge.
   - Verify promo buttons do not clutter the topbar when sorting local music folders like `sparks`.
   - Click `#btn-home` in local sorting -> immediately returns to Welcome Hub.
   - Click `⚡ Review All Promos` -> starts promo review -> click `#btn-home` -> immediately returns to Welcome Hub.
