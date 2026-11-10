# PLN-07: Welcome Hub Redesign, In-App SQLite Comments Editor & Configurable Promo Ratings

- **Feature ID**: PLN-07
- **Status**: Completed
- **Scope**: `bakkis_sorter/web/index.html`, `bakkis_sorter/web/app.css`, `bakkis_sorter/web/comments.js`, `bakkis_sorter/web/app.js`, `bakkis_sorter/storage/reviews.py`, `bakkis_sorter/storage/__init__.py`, `bakkis_sorter/promos/comments.py`, `bakkis_sorter/promos/session.py`, `bakkis_sorter/promos/models.py`, `bakkis_sorter/config.py`, `bakkis_sorter/backend/promos.py`, `tests/test_comments_storage.py`
- **Target Test Markers**: `pytest -m storage -v` & `pytest -m promos -v` & `pytest -m frontend -v`
- **Target Execution Speed**: < 0.80s
- **Storage Target**: SQLite `promo_comments` table in `bakkis.db` + `settings.json`
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & Motivation

Currently, the landing/welcome screen, comment editing, and promo ratings suffer from four usability limitations:
1. **Button Collision & Visual Overlap**:
   - In `app.css`, `.auth-row` has `margin-top: -6px;`.
   - When the window resizes or wraps, the secondary service connection pills (`Inflyte · Connected` / `Trackstack · Connected`) physically collide and overlap with `⚡ Review All Promos` and `Choose music folder`.
2. **Visual Clutter & Lack of Hierarchy**:
   - The landing screen mixes 5 buttons in flat rows with conflicting visual priorities (`Review All Promos`, `Choose music folder`, `Edit promo_comments.txt`, `Inflyte login`, `Trackstack login`).
   - The user needs two clear, distinct primary paths: **Review Promos** vs. **Sort Music**.
3. **External Editor Dependency for Comments**:
   - Clicking `Edit promo_comments.txt` executes `subprocess.Popen(["open", "-t", ...])` to launch Apple TextEdit.app.
   - Comments should be managed directly within the application and persisted securely in the SQLite database (`bakkis.db`), decoupling the app from loose filesystem files.
4. **Hardcoded Rating Numbers for Inflyte**:
   - In `session.py`, slot 2 (Up arrow / "Feeling it") is hardcoded to `5` (`rate_5`), and slot 0 (Right arrow / "Keep") is hardcoded to `4` (`rate_4`).
   - DJs have differing rating philosophies: some prefer sending `4/5` for favorites and `3/5` for keep, or `5/5` for favorites and `3/5` for keep. Users need configurable rating numbers for both slots.

---

## 2. Technical Objectives & Design

### 2.1 Logo Tagline Update
Update the hero brand lockup tagline in `bakkis_sorter/web/index.html`:
```html
<p class="slogan">Fast.&nbsp; · &nbsp;Instinctive.&nbsp; · &nbsp;Sorting.&nbsp; · &nbsp;<span>Promo Mgmt.</span></p>
```

---

### 2.2 Welcome Hub 2-Pillar Redesign
Eliminate all overlapping margins. Restructure the welcome screen into two distinct, balanced visual cards side-by-side using CSS Grid:

```
+------------------------------------------------+------------------------------------------------+
|                 ⚡ REVIEW PROMOS                |                    📁 SORT MUSIC                |
|           Inflyte Promo Box & Trackstack       |           Organize & slice local DJ tracks     |
|                                                |                                                |
|     [     ⚡ Review All Promos (Primary)     ]   |     [     Choose Music Folder (Ghost)        ]   |
|                                                |                                                |
|     • Inflyte · Connected                      |         or drop a folder anywhere in this window|
|     • Trackstack · Connected                    |         Sorted tracks go to BAKKIS_SORTED      |
+------------------------------------------------+------------------------------------------------+
```

#### HTML Structure (`bakkis_sorter/web/index.html`):
```html
<div class="welcome-hub">
  <!-- Pillar 1: Promo Hub -->
  <div class="welcome-card welcome-card-promos">
    <div class="welcome-card-head">
      <div class="welcome-card-title-row">
        <span class="welcome-badge">⚡ Promos</span>
      </div>
      <h2>Review Promos</h2>
      <p class="muted small">Inflyte Promo Box & Trackstack</p>
    </div>
    <button type="button" class="primary-btn welcome-action-btn" id="btn-load-inflyte">
      ⚡ Review All Promos
    </button>
    <div class="welcome-auth-pills" aria-label="Promo service connections">
      <button type="button" class="ghost-btn auth-pill" id="btn-login-inflyte" title="Click to log into Inflyte Promo Box">
        <span class="status-dot" id="dot-main-inflyte"></span>
        <span id="lbl-main-inflyte">Inflyte · Log In</span>
      </button>
      <button type="button" class="ghost-btn auth-pill" id="btn-login-trackstack" title="Click to log into Trackstack">
        <span class="status-dot" id="dot-main-trackstack"></span>
        <span id="lbl-main-trackstack">Trackstack · Log In</span>
      </button>
    </div>
  </div>

  <!-- Pillar 2: Sorter Hub -->
  <div class="welcome-card welcome-card-sorter">
    <div class="welcome-card-head">
      <div class="welcome-card-title-row">
        <span class="welcome-badge">📁 Sorter</span>
      </div>
      <h2>Sort Music</h2>
      <p class="muted small">Organize & slice local DJ tracks</p>
    </div>
    <button type="button" class="ghost-btn welcome-action-btn" id="btn-choose">
      Choose Music Folder
    </button>
    <div class="welcome-drop-info">
      <p class="muted">or drop a folder anywhere in this window</p>
      <p class="muted small">Sorted tracks go to <code>BAKKIS_SORTED</code> (copies to <code>BAKKIS_SORTED_COPIES</code>)</p>
    </div>
  </div>
</div>
```

#### CSS Styling (`bakkis_sorter/web/app.css`):
- CSS Grid with `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; max-width: 780px; width: 100%; margin: 24px auto 0;`.
- `.welcome-card`: Dark panel background (`var(--panel)`), border `1px solid var(--line-strong)`, border-radius `12px`, padding `24px`, flex column layout.
- `.welcome-action-btn`: 100% card width, large touch target (height `42px`), bold text.
- Remove all negative margins (`margin-top: -6px` is removed completely).

---

### 2.3 SQLite Storage for Promo Comments (`bakkis.db`)

#### A. Database Schema (`bakkis_sorter/storage/__init__.py` & `reviews.py`)
```sql
CREATE TABLE IF NOT EXISTS promo_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rating INTEGER NOT NULL,  -- 5 (favorite) or 4 (keep)
    comment TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promo_comments_rating ON promo_comments (rating);
```

#### B. Storage Methods (`bakkis_sorter/storage/reviews.py`)
- `get_promo_comments() -> dict[int, list[str]]`: Returns `{ 5: [...], 4: [...] }`.
- `set_promo_comments(pools: dict[int, list[str]]) -> None`: Replaces comments transactionally.
- **Auto-Migration**: If table is empty on first boot, it reads `promo_comments.txt` (or defaults) and populates the table.

#### C. Backend API Integration (`bakkis_sorter/backend/promos.py`)
- `api_promo_comments_get() -> dict[str, Any]`: Returns comments pools `{ "5": [...], "4": [...] }` and current rating numbers `{ "promo_rating_favorite": 5, "promo_rating_keep": 4 }`.
- `api_promo_comments_save(comments_5: list[str], comments_4: list[str], rating_favorite: int = 5, rating_keep: int = 4) -> bool`:
  - Sanitizes and persists comments to SQLite.
  - Updates `promo_rating_favorite` and `promo_rating_keep` in user settings.
  - Writes a backup to `promo_comments.txt`.

#### D. Comment Selector (`bakkis_sorter/promos/comments.py`)
- `CommentPool.load_pools()` reads directly from SQLite via `storage.get_promo_comments()`.
- Thread-safe random rotation with repeat prevention.

---

### 2.4 Configurable Inflyte Rating Numbers

#### A. Settings Configuration (`bakkis_sorter/config.py`)
Add two settings keys to `default_settings()` and `normalize_settings()`:
- `promo_rating_favorite: int = 5` (Allowed: `1..5`, default: `5`). Rating sent for "Feeling this" (Up arrow / slot 2).
- `promo_rating_keep: int = 4` (Allowed: `1..5`, default: `4`). Rating sent for "Keep / Maybe" (Right arrow / slot 0).

#### B. Session Feedback Dispatch (`bakkis_sorter/promos/session.py`)
In `InflyteSession._execute_decision_sync`:
```python
settings_dict = self._settings() if callable(self._settings) else {}
rating_fav = max(1, min(5, int(settings_dict.get("promo_rating_favorite", 5))))
rating_keep = max(1, min(5, int(settings_dict.get("promo_rating_keep", 4))))

if promo.source == "trackstack":
    rating = None
    comment = None
    kind = f"rate_{rating_fav}" if slot_i == 2 else (f"rate_{rating_keep}" if slot_i == 0 else "dismiss")
elif slot_i == 2:
    rating = rating_fav
    comment = self.comments.pick(5)  # Pick from Favorite / Feeling this pool
    kind = f"rate_{rating_fav}"
elif slot_i == 0:
    rating = rating_keep
    comment = self.comments.pick(4)  # Pick from Keep / Maybe pool
    kind = f"rate_{rating_keep}"
else:
    rating = None
    comment = None
    kind = "dismiss"
```

All downstream checks (`a.kind in ("rate_5", "rate_4")`) are updated to `a.kind.startswith("rate_")` or whitelisted `("rate_1", "rate_2", "rate_3", "rate_4", "rate_5")`.

---

### 2.5 In-App Comments & Ratings Editor Dialog

#### UI Design:
A clean modal dialog (`id="modal-comments"`) in `index.html`:
- **Header**:
  - Title: `Feedback Comments & Ratings`
  - Subtitle: `Configure auto-feedback comments and numeric stars submitted to Inflyte.`
- **Rating Selectors Row**:
  - **Feeling this (Up Arrow)**: Select dropdown `[5/5 ⭐⭐⭐⭐⭐, 4/5 ⭐⭐⭐⭐, 3/5 ⭐⭐⭐]` (default: `5`).
  - **Keep / Maybe (Right Arrow)**: Select dropdown `[5/5 ⭐⭐⭐⭐⭐, 4/5 ⭐⭐⭐⭐, 3/5 ⭐⭐⭐, 2/5 ⭐⭐, 1/5 ⭐]` (default: `4`).
- **Favorite Comments Textarea**:
  - Label: `Feeling this (Favorite) Comments — one per line`
  - Rows: 6
- **Keep Comments Textarea**:
  - Label: `Keep / Maybe Comments — one per line`
  - Rows: 6
- **Buttons**:
  - `Cancel` (ghost)
  - `Save Changes` (primary)
- **Toast Feedback**: Displays `Comments & ratings saved` on save.
- **Access Points**:
  - Header / Landing screen button (`#btn-edit-comments` or settings menu)
  - Settings drawer section: "Promo Feedback & Ratings"

---

### 2.6 Character & Encoding Sanitation Policy

DJs frequently copy and paste feedback containing emojis, European accents, smart quotes from mobile keyboards, or invisible control characters. To prevent SQLite errors (`sqlite3.ProgrammingError`), JSON serialization issues, or Inflyte API rejections, the engine enforces strict sanitization:

```python
def sanitize_comment(text: str, max_len: int = 500) -> str:
    """Normalize unicode, strip null bytes & control chars, preserve emojis/accents, and clamp length."""
    if not isinstance(text, str):
        return ""
    # 1. Replace illegal lone surrogates safely
    safe = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    # 2. Strip null bytes and non-printable control chars (preserve tabs/newlines/emojis)
    cleaned = "".join(c for c in safe if c == " " or (c != "\x00" and not (ord(c) < 32 and c not in "\t\n")))
    # 3. Unicode NFC composition
    normalized = unicodedata.normalize("NFC", cleaned).strip()
    # 4. Length clamping to respect API limits (max 500 chars)
    return normalized[:max_len]
```

- **Supported & Preserved**:
  - Full Unicode emojis: 🔥, 💣, 🚀, 🙌, 🎧, 🔊
  - International diacritics: é, è, ü, ö, ä, ñ, ø, å
  - Typographic smart quotes & dashes: “ ”, ‘ ’, —, …
- **Sanitized & Stripped**:
  - Null bytes (`\x00` terminates C strings and crashes SQLite)
  - ASCII control characters (bell, backspace, escape codes)
  - Lone surrogate pairs (`\ud800`) safely replaced with replacement characters
  - Excessive character length clamped to 500 characters

---

### 2.7 SQL Injection Prevention Architecture

To prevent SQL injection attacks through user-edited comments or API payloads, the engine enforces a strict multi-layered defense:
1. **Parameterized Queries (`?` Bind Placeholders)**:
   - Data is transferred to the SQLite virtual database engine (VDBE) out-of-band as literal text.
   - The query syntax tree is compiled before parameters are bound.
   - Attack strings like `'); DROP TABLE promo_comments; --` are stored safely as literal strings inside the text column.
2. **Multi-Statement Blockade**:
   - Python's `sqlite3.Cursor.execute()` enforces single-statement execution and immediately raises `sqlite3.Warning` if semicolons separate chained queries.
3. **Rating Type & Value Whitelisting**:
   - `rating` is strictly validated: `assert rating in (1, 2, 3, 4, 5)` and cast to `int`. Non-integer or out-of-bounds ratings are rejected before reaching SQL execution.
4. **Thread-Safe Transaction Rollback**:
   - Write operations execute within `with self._lock:` and call `self._conn.commit()`. Any malformed or rejected statement triggers automatic rollback.

---

## 3. Component Modifications Detail

| File | Changes |
| :--- | :--- |
| **`bakkis_sorter/web/index.html`** | 1. Update tagline to `Fast. · Instinctive. · Sorting. · Promo Mgmt.`.<br>2. Replace crowded buttons with 2-Pillar Welcome Hub.<br>3. Add `#modal-comments` dialog with rating number selectors & comment textareas. |
| **`bakkis_sorter/web/app.css`** | 1. Add `.welcome-hub`, `.welcome-card`, `.welcome-action-btn`.<br>2. Remove `.auth-row` negative margin.<br>3. Add styles for `#modal-comments` selectors and textareas. |
| **`bakkis_sorter/web/panels.js`** | 1. Add `initCommentsModal()` logic (load ratings & comments, save, cancel).<br>2. Wire `#btn-edit-comments` to open `#modal-comments`. |
| **`bakkis_sorter/config.py`** | Add `promo_rating_favorite` (default 5) and `promo_rating_keep` (default 4) to `default_settings()` and `normalize_settings()`. |
| **`bakkis_sorter/promos/models.py`** | Expand `InflyteAction.kind` to allow `rate_1` through `rate_5`. |
| **`bakkis_sorter/promos/session.py`** | Use configured `promo_rating_favorite` and `promo_rating_keep` when recording and submitting reviews; change exact `in ("rate_5", "rate_4")` checks to `a.kind.startswith("rate_")`. |
| **`bakkis_sorter/storage/__init__.py`** | Add `promo_comments` table and index creation. |
| **`bakkis_sorter/storage/reviews.py`** | Add `get_promo_comments` and `set_promo_comments`. |
| **`bakkis_sorter/backend/promos.py`** | Add `api_promo_comments_get` and `api_promo_comments_save` handling comments and rating numbers. |
| **`bakkis_sorter/promos/comments.py`** | Update `CommentPool` to read from SQLite with fallback and include `sanitize_comment`. |
| **`tests/test_comments_storage.py`** | Unit tests for comments schema, roundtrip, configurable ratings, auto-migration, encoding/emoji fidelity, and SQL injection resilience. |

---

## 4. Verification & Testing Plan

1. **Storage & Config Unit Tests (`tests/test_comments_storage.py`)**:
   - `test_promo_comments_storage_roundtrip`: Test storing and retrieving 5/5 and 4/5 comments.
   - `test_promo_comments_migration_from_default`: Test automatic migration when database is empty.
   - `test_backend_promo_comments_api`: Test `api_promo_comments_get` and `api_promo_comments_save` with comments and custom ratings (e.g. 5/5 -> 4/5, 4/5 -> 3/5).
   - `test_session_uses_custom_ratings`: Verify `session.py` dispatches the user-configured rating numbers (`promo_rating_favorite`, `promo_rating_keep`) to the bridge.
   - `test_promo_comments_encoding_and_unsupported_characters`:
     - Test emojis (🔥, 💣, 🎧).
     - Test international characters (Umlauts, Cyrillic, CJK, accents).
     - Test smart quotes, typographic dashes, and symbols.
     - Test null byte (`\x00`) and ASCII control characters are safely stripped without crashing SQLite.
     - Test lone surrogate pair (`\ud800`) replacement without `UnicodeEncodeError`.
     - Test extreme length clamping (> 500 characters).
   - `test_promo_comments_sql_injection_resilience`:
     - Test malicious payloads: `'); DROP TABLE promo_comments; --`, `' OR 1=1 --`, `UNION SELECT...`, `Robert'); DROP TABLE Students;--`.
     - Verify table remains intact and payloads are persisted harmlessly as literal data.
2. **Frontend Asset Tests (`tests/test_web_modules.py`)**:
   - Verify markup and module integrity.
3. **Execution Targets**:
   - `pytest -m "storage or promos" -v` in **< 1.0s**.
   - Full suite `pytest -q` (**100% pass**, all 268+ tests).
