# PLN-12: Frontend Architecture & Style Modularization (Phase 2)

- **Feature ID**: PLN-12
- **Version Bump**: `2.1.3` -> `2.1.4`
- **Status**: Completed
- **Scope**:
  - `bakkis_sorter/__init__.py` (version bump to `2.1.4`)
  - `PLAN.md`
  - `bakkis_sorter/web/panels.js` ➔ `bakkis_sorter/web/drawers/`:
    - `settings.js` (`SettingsDrawer` & `SETTINGS_SCHEMA`)
    - `presets.js` (`PresetEditor` & swatch management)
    - `history.js` (`HistoryDrawer` for local sessions)
    - `promo_history.js` (`PromoHistoryDrawer`)
    - `modals.js` (`Guide` shortcuts & `Welcome` intro dialogs)
    - `panels.js` (lean coordinator, `Drawers.show`, `Drawers.close`, `window.*` export binder)
  - `bakkis_sorter/web/app.css` ➔ `bakkis_sorter/web/css/`:
    - `base.css` (tokens, CSS variables, resets, buttons, form controls)
    - `player.css` (player stage, waveform canvas, swipe & grid decision tiles, hints, footer)
    - `hub.css` (2-pillar welcome hub, cards, badges, promo caught-up banner, comments modal)
    - `drawers.css` (drawer shell, queue list, settings, presets, history lists)
    - `responsive.css` (media queries for half-screen, tablet, and mobile)
    - `app.css` (lean index importing the domain stylesheets via `@import`)
  - Tests:
    - `tests/test_web_modules.py`
    - `tests/test_server.py`
- **Target Test Markers**: `pytest -m "frontend or server" -v`
- **Target Execution Speed**: < 1.0s
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement

Two frontend assets have grown excessively large:
1. **`bakkis_sorter/web/panels.js` (1,113 lines)**:
   A single file bundling 6 completely different controllers (`SettingsDrawer`, `PresetEditor`, `HistoryDrawer`, `PromoHistoryDrawer`, `Guide`, `Welcome`) together with the global drawer state machine (`Drawers`).
2. **`bakkis_sorter/web/app.css` (1,161 lines)**:
   A massive monolithic stylesheet mixing typography, core player styling, welcome hub components, multiple drawers, modals, and half-screen responsive breakpoints.

---

## 2. Architecture & Modular Boundary Design

### 2.1 Drawers Modularization (`bakkis_sorter/web/drawers/`)

```
bakkis_sorter/web/
├── drawers/
│   ├── settings.js       # SettingsDrawer, SETTINGS_SCHEMA, view rendering, reset actions
│   ├── presets.js        # PresetEditor, new preset creation, color swatch pickers
│   ├── history.js        # HistoryDrawer (local session history, restore / put back)
│   ├── promo_history.js  # PromoHistoryDrawer (promo review list, search, filters)
│   └── modals.js         # Guide (keyboard shortcuts dialog) & Welcome (intro card)
└── panels.js             # Drawer coordinator (Drawers.show, Drawers.close, setupPanels)
```

- **`window.*` Compatibility**:
  To guarantee complete backwards compatibility with `app.js` and inline callers, each drawer controller will remain bound to `window`:
  - `window.SettingsDrawer = SettingsDrawer;`
  - `window.PresetEditor = PresetEditor;`
  - `window.HistoryDrawer = HistoryDrawer;`
  - `window.PromoHistoryDrawer = PromoHistoryDrawer;`
  - `window.Guide = Guide;`
  - `window.Welcome = Welcome;`
  - `window.Drawers = Drawers;`

---

### 2.2 Stylesheet Segmentation (`bakkis_sorter/web/css/`)

```
bakkis_sorter/web/
├── css/
│   ├── base.css        # CSS variables, typography, resets, buttons, icons
│   ├── player.css      # Player screen, waveform canvas, swipe tiles, grid tiles, hints, footer
│   ├── hub.css         # Welcome Hub, cards, status pills, caught-up banner, comments dialog
│   ├── drawers.css     # Drawer container styles, queue, history, settings, presets, promo cards
│   └── responsive.css  # Media queries (960px, 768px, 680px, 640px)
└── app.css             # Root stylesheet composing all domain CSS via @import
```

By utilizing `@import` inside `app.css`:
```css
@import "./css/base.css";
@import "./css/player.css";
@import "./css/hub.css";
@import "./css/drawers.css";
@import "./css/responsive.css";
```
- The browser and WebKit UI load `app.css` without modifying any server route or `index.html` link headers.
- Each segmented file stays between 150 to 300 lines, easily navigable and maintained.

---

## 3. Version Bump: `2.1.3` ➔ `2.1.4`

- Increment `__version__ = "2.1.4"` in `bakkis_sorter/__init__.py`.
- Update `PLAN.md` with the release row for `v2.1.4`.
- Update assertions in `tests/test_web_modules.py` to verify `__version__ == "2.1.4"`.

---

## 4. Verification Plan

1. **Module Import & Static Server Tests**:
   - `test_all_frontend_modules_exist`: verifies all `web/drawers/*.js` and `web/css/*.css` exist.
   - `test_static_server_serves_all_modules_with_correct_mime`: ensures Python's HTTP server serves `.css` with `text/css` and `.js` with `application/javascript`.
   - `test_panels_exposes_window_controllers`: verifies `window.Drawers`, `window.SettingsDrawer`, etc.
2. **Full Regression Suite**:
   - Run `pytest -q` across the entire project (all 282+ tests must pass).
