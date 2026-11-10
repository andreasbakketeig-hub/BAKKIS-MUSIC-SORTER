# PLN-08: Settings Drawer Activation & Responsive Half-Screen Layout

- **Feature ID**: PLN-08
- **Status**: Completed
- **Scope**: 
  - `bakkis_sorter/web/panels.js`
  - `bakkis_sorter/web/app.css`
  - `bakkis_sorter/web/index.html`
  - `bakkis_sorter/web/app.js`
  - `tests/test_web_modules.py`
- **Target Test Markers**: `pytest -m frontend -v`
- **Target Execution Speed**: < 0.40s
- **Storage Target**: N/A (Frontend UX & Responsive Layout)
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Motivation

Users testing BAKKIS on macOS reported two key issues:
1. **Settings / Configuration Button Does Not Open**:
   - In the top bar, clicking `#btn-settings` does nothing.
   - Root Cause: `panels.js` is loaded as a classic `<script>`, where top-level `const Drawers = { ... }` and `const SettingsDrawer = { ... }` are placed in the Declarative Environment Record and never bound to `window`.
   - When `app.js` (an ES Module) executes `window.Drawers.toggle("settings")`, `window.Drawers` and `window.SettingsDrawer` evaluate to `undefined`, causing the click handlers to fail silently.
2. **Half-Screen Layout Overflow & Cut-Off**:
   - When the user runs the app in macOS Split View or half-screen (e.g. 600px–850px wide):
     - The top bar overflows horizontally, pushing the action buttons (`#btn-settings`, `#btn-history`, `#btn-queue`, `#btn-help`) offscreen.
     - `.empty-inner` was constrained to `max-width: 520px;`, squeezing the new 2-pillar welcome hub cards into narrow squished boxes.
     - `.screen-player` had `clamp(960px, 70vw, 1280px)`, which assumed a minimum window width of 960px, clipping card contents, waveform canvas, and decision tiles in half-screen.
     - `.tiles-swipe` had `repeat(4, 1fr)`, squeezing 4 tiles into ~100px wide columns in half-screen.

---

## 2. Technical Solution & Architecture

### 2.1 Window Property Binding in `panels.js`
Explicitly bind all panel objects to `window` at the end of `panels.js`:
```javascript
// Expose panel controllers to ES module app.js
window.Drawers = Drawers;
window.SettingsDrawer = SettingsDrawer;
window.HistoryDrawer = HistoryDrawer;
window.PresetEditor = PresetEditor;
window.Guide = Guide;
window.Welcome = Welcome;
```
This restores instant functionality to:
- `#btn-settings` (Settings Drawer)
- `#btn-queue` (Queue Drawer)
- `#btn-history` (History Drawer)
- `#btn-help` (Keyboard Shortcuts Guide)
- `#op-badge` (Direct jump to Settings > File Operation)
- `#filter-badge` (Direct jump to Queue Drawer)

In addition, add a direct configuration link on the Welcome Hub's Sorter Card (`#btn-sorter-settings`) so users can configure sorting presets, file operation (Copy vs Move), and playback start without having to find the top bar icon.

---

### 2.2 Responsive Top Bar (`app.css`)
Ensure the top bar adapts gracefully from 500px to 2560px without pushing buttons offscreen:
- Pin `.top-icons` to the right with `margin-left: auto; flex: none; display: flex; gap: 4px;`.
- At `@media (max-width: 920px)`:
  - Collapse text on secondary top chips (`💬 Comments` becomes `💬`, `✈️ Stash` becomes `✈️`).
  - Constrain `.folder-chip` to `max-width: 140px;`.
  - Constrain `#preset-select` to `max-width: 130px;`.
- At `@media (max-width: 760px)`:
  - Hide `.top-progress` (`display: none;`) to yield space to essential controls.
  - Collapse `.brand` lockup to just the mark logo without descriptor.

---

### 2.3 Responsive Welcome Hub & Sorter Screen
1. **Welcome Screen (`.empty-inner`, `.welcome-hub`)**:
   - Expand `.empty-inner` from `max-width: 520px;` to `max-width: 820px; width: 92%; margin: 0 auto;`.
   - On screens `< 700px`, `.welcome-hub` automatically stacks vertically with full card width.
2. **Player Screen (`.screen-player`)**:
   - Change padding to dynamic fluid clamp:
     ```css
     padding: 16px max(16px, calc((100% - clamp(540px, 85vw, 1280px)) / 2));
     ```
   - On screens `< 768px` wide or `< 700px` tall:
     - Card cover scales from `112px` to `80px`.
     - Track title scales from `var(--fs-xl)` (24px) to `var(--fs-l)` (18px).
     - Waveform height scales to `clamp(64px, 11vh, 110px)`.
     - Volume slider shortens to `80px`.
3. **Swipe Decision Tiles (`.tiles-swipe`)**:
   - On screens `< 640px`:
     - Switch `.tiles-swipe` from 4 flat columns to a 2x2 grid (`grid-template-columns: repeat(2, 1fr)`).
     - This gives each tile ample room (250px+ width) so the icon, label, and keyboard shortcut badge fit comfortably.

---

### 2.4 Responsive Modals & Drawers
1. **Dialogs (`.modal`, `.comments-dialog`)**:
   - Add `max-height: 90vh; overflow-y: auto;` to `.modal`.
   - On screens `< 640px`:
     - `.ratings-config-row` and `.comments-pool-row` stack to 1 column (`grid-template-columns: 1fr; gap: 12px;`).
2. **Drawers (`.drawer`)**:
   - On screens `< 600px`:
     - `.drawer` takes `width: 100vw;` with full-screen focus.

---

## 3. Component Modifications Detail

| File | Changes |
| :--- | :--- |
| **`bakkis_sorter/web/panels.js`** | Attach `window.Drawers`, `window.SettingsDrawer`, `window.HistoryDrawer`, `window.PresetEditor`, `window.Guide`, `window.Welcome`. |
| **`bakkis_sorter/web/app.js`** | Wire `#btn-sorter-settings` on welcome screen to open Settings Drawer. |
| **`bakkis_sorter/web/index.html`** | Add `#btn-sorter-settings` link under Sort Music card in Welcome Hub. |
| **`bakkis_sorter/web/app.css`** | 1. Fix `.empty-inner` max-width to 820px.<br>2. Add fluid `.screen-player` clamping.<br>3. Add media queries for topbar, cards, swipe 2x2 grid, and modals. |
| **`tests/test_web_modules.py`** | Add unit tests verifying `window` properties on `panels.js` and responsive CSS rules. |

---

## 4. Verification & Testing Plan

1. **Frontend Asset Tests (`tests/test_web_modules.py`)**:
   - Verify `panels.js` defines window bindings for `Drawers`, `SettingsDrawer`, `HistoryDrawer`, `PresetEditor`, `Guide`, `Welcome`.
   - Verify `app.css` contains half-screen media queries (`@media (max-width: 760px)`, `@media (max-width: 640px)`).
2. **Functional UI Test**:
   - Click `#btn-settings` -> Settings drawer opens.
   - Click `#btn-sorter-settings` -> Settings drawer opens to presets/sorting.
   - Resize window to 600px (half screen) -> All buttons stay visible, swipe tiles switch to 2x2 layout, zero horizontal cut-off.
3. **Execution Target**:
   - `pytest -m frontend -v` in **< 0.40s**.
   - Full suite `pytest -q` (**100% pass**, all 275+ tests).
