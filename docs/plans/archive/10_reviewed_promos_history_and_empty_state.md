# PLN-10: Promo Feedback History & Caught-Up Empty State (v2.1.3)

- **Feature ID**: PLN-10
- **Version Bump**: `2.1.2` -> `2.1.3`
- **Status**: Completed (Verified - 282 tests passing)
- **Scope**:
  - `bakkis_sorter/__init__.py`
  - `PLAN.md`
  - `bakkis_sorter/storage/reviews.py`
  - `bakkis_sorter/backend/promos.py`
  - `bakkis_sorter/promos/session.py`
  - `bakkis_sorter/web/index.html`
  - `bakkis_sorter/web/app.css`
  - `bakkis_sorter/web/app.js`
  - `bakkis_sorter/web/panels.js`
  - `tests/test_storage_reviews.py` (or `test_comments_storage.py`)
  - `tests/test_web_modules.py`
  - `tests/test_server.py`
- **Target Test Markers**: `pytest -m "promos or storage or frontend or server"`
- **Target Execution Speed**: < 1.0s
- **Author**: BAKKIS Core Team

---

## 1. Problem Statement & User Experience Objectives

### 1.1 The Empty Backlog Gap
When a user launches **Review All Promos** (`⚡ Review All Promos`) after having already sorted/reviewed their promo inbox, the current system:
1. Concludes the scan with `session.total == 0`.
2. Falls back to `.screen-empty` and prints a generic local folder error message:
   `No audio files found in All Promos (Inflyte + Trackstack). Supported: mp3, wav, aiff...`
3. Gives zero feedback that the promo boxes were successfully checked and found to be 100% caught up.
4. Provides no mechanism to inspect previously submitted promo feedback, ratings, comments, or downloaded files.

### 1.2 User Requirements
1. **Clear Zero-State Messaging**: Explicitly inform the user when zero tracks are awaiting feedback across Inflyte and Trackstack ("All Caught Up! No promos awaiting feedback").
2. **Review Previous Feedback**: Allow users to browse and review their past promo feedback history, including submitted ratings, comments, dates, decisions (favorites, keeps, dismissals), and downloaded audio files.
3. **Landing Screen Access**: Enable reviewing past promo feedback directly from the Welcome Hub as well as from the caught-up state.
4. **Version Bump**: Update the application version to `2.1.3` across `bakkis_sorter/__init__.py` and project documentation.

---

## 2. Technical Architecture & Component Design

```
┌────────────────────────────────────────────────────────────────────────┐
│                          WEB CLIENT (WebKit UI)                        │
├────────────────────────────────────────────────────────────────────────┤
│  [Welcome Hub]                                                         │
│    • "⚡ Review All Promos"                                            │
│    • "📜 Review past promo feedback" ──────────────────────┐           │
│                                                            │           │
│  [Promo Caught-Up View]                                    │           │
│    • "✓ All Caught Up! No promos awaiting feedback"        │           │
│    • [📜 Review Past Promo Feedback] ──────────────────────┼───────────┤
│    • [🔄 Re-check Inboxes]                                 │           │
│                                                            │           │
│  [Promo Review History Drawer] <───────────────────────────┘           │
│    • Filter: All | Inflyte | Trackstack | 5★ | 4★ | Dismissed          │
│    • Search bar: Filter by artist, track title, promo, comment         │
│    • Items: Badge, Artist/Title, Rating/Comment, Timestamp, Reveal DL  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ JSON RPC (/api/promo_history)
┌────────────────────────────────────▼───────────────────────────────────┐
│                      BACKEND (AppBackend & PromosApiMixin)              │
├────────────────────────────────────────────────────────────────────────┤
│  `api_promo_history(limit, offset, query, source, decision)`           │
│    • Fetches decrypted reviews from SQLite storage                      │
│    • Correlates signatures with track artist & title                    │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ EncryptedStorage / AES-256 GCM
┌────────────────────────────────────▼───────────────────────────────────┐
│                       SQLITE STORAGE (`bakkis.db`)                     │
├────────────────────────────────────────────────────────────────────────┤
│  `promo_reviews` (ciphertext decrypted with macOS Keychain master key) │
│  `promo_track_signatures` (artist, title, decision, rating, timestamp) │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Implementation Steps

### 3.1 Step 1: Version Bump to `2.1.3`
- Update `bakkis_sorter/__init__.py`: `__version__ = "2.1.3"`.
- Update `PLAN.md`: Add v2.1.3 entry and mark current status.

### 3.2 Step 2: Storage Layer (`bakkis_sorter/storage/reviews.py`)
Add method `get_promo_review_history` to `ReviewStorageMixin`:
```python
def get_promo_review_history(
    self,
    limit: int = 100,
    offset: int = 0,
    source: Optional[str] = None,
    decision: Optional[str] = None,
    search: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Retrieve decrypted promo reviews ordered by reviewed_at DESC with optional filtering."""
```
- Query `promo_reviews` joined or paired with `promo_track_signatures` for rich artist/title data.
- Decrypt `ciphertext` via `self.cipher.decrypt_json()`.
- Return list of structured review dictionaries and total count:
  - `source`: `"inflyte"` | `"trackstack"`
  - `promo_key`: string release ID / slug
  - `promo_name`: release name
  - `artist`: artist name
  - `title`: track title
  - `decision`: `"rate_5"` | `"rate_4"` | `"dismiss"` | `"support"` | `"decline"`
  - `rating`: integer rating (5, 4, etc.)
  - `comment`: submitted text comment
  - `downloaded`: list of downloaded file paths
  - `reviewed_at`: UNIX timestamp

### 3.3 Step 3: Session Data Enrichment (`bakkis_sorter/promos/session.py`)
In `InflyteSession._execute_action`:
- Ensure `artist` and `title` from `action.track` are included in `details` dictionary saved to `record_review`:
  ```python
  details = {
      "promo_name": a.promo.name,
      "artist": a.track.artist,
      "title": a.track.title,
      "rating": a.rating,
      "comment": a.comment,
      "downloaded": [str(p) for p in saved_files],
  }
  ```

### 3.4 Step 4: Backend API Endpoint (`bakkis_sorter/backend/promos.py`)
Add `api_promo_history` to `PromosApiMixin`:
```python
def api_promo_history(
    self,
    limit: int = 100,
    offset: int = 0,
    source: str = "",
    decision: str = "",
    search: str = "",
) -> dict:
    """Return decrypted past promo reviews and summary counts."""
```
- Returns `{"reviews": list, "total": int}`.

### 3.5 Step 5: Frontend Caught-Up UI & History Drawer (`index.html` & `app.css`)
1. **Promo Caught-Up View (`#screen-promo-empty`)**:
   - Distinct view or contextual state in `.screen-empty` when promo scan finishes with 0 pending tracks:
     - Icon: Large glowing lightning/checkmark badge (`⚡✓`).
     - Heading: `All Caught Up!`
     - Subtitle: `No promo tracks are currently awaiting feedback in Inflyte or Trackstack.`
     - Action 1: `📜 Review Previous Feedback` (opens promo history drawer).
     - Action 2: `🔄 Check Again` (re-runs `loadInflyte("all")`).
     - Action 3: `🏠 Return to Welcome Hub` (`goHome()`).
2. **Promo History Drawer (`#drawer-promo-history`)**:
   - Header with search input and filter chips:
     - `[All]` `[Inflyte]` `[Trackstack]` `[Favorites ★5]` `[Keeps ★4]` `[Dismissed]`
   - Scrollable card list with:
     - Service pill (`Inflyte` / `Trackstack`)
     - Formatted review timestamp (e.g. `Nov 9, 2026, 7:15 PM`)
     - Track Title & Artist
     - Rating badge (`⭐⭐⭐⭐⭐ 5/5` or `⭐⭐⭐⭐ 4/5` or `👎 Dismissed / Declined`)
     - Feedback text quote block: `“...”`
     - Delivery / Download status:
       - **Trackstack**: `✓ Downloaded to ~/Music/Inflyte Music`
       - **Inflyte**: `⚡ Auto-synced to Inflyte Dropbox` (with `📁 On disk` tag if found in local library)
3. **Welcome Hub Link**:
   - In `.welcome-card-promos`, add:
     `<button type="button" class="text-link-btn" id="btn-promo-history">📜 Review previous feedback</button>`.

### 3.6 Step 6: Frontend Controller (`bakkis_sorter/web/panels.js` & `app.js`)
1. Define `PromoHistoryDrawer` in `panels.js`:
   - `render()`: calls `api("promo_history")` and populates the list.
   - Filter handling and real-time search filtering.
   - Expose `window.PromoHistoryDrawer = PromoHistoryDrawer`.
2. In `app.js`:
   - In `renderAll()`: if `sess && sess.is_inflyte && sess.total === 0`, show `#promo-caught-up` view instead of "No audio files found".
   - Wire `#btn-promo-history` and caught-up action buttons to open `PromoHistoryDrawer`.

---

## 4. Test Verification Plan

1. **Storage Unit Tests (`tests/test_storage_reviews.py` or `test_comments_storage.py`)**:
   - Record multiple reviews (Inflyte 5/5 with comment, Trackstack with downloads, dismissed promos).
   - Test `get_promo_review_history()` returns decrypted data ordered by `reviewed_at DESC`.
   - Test filtering by source (`inflyte` vs `trackstack`) and search queries.
2. **API Unit Tests (`tests/test_server.py`)**:
   - Test `api_promo_history` endpoint returns JSON status 200 and formatted reviews.
3. **Frontend Integration Tests (`tests/test_web_modules.py`)**:
   - Assert `#drawer-promo-history`, `#btn-promo-history`, and `#promo-caught-up` elements exist in `index.html`.
   - Assert `PromoHistoryDrawer` exported in `panels.js`.
   - Assert version is `2.1.3` in `bakkis_sorter/__init__.py`.
4. **Full Regression Suite**:
   - Run `pytest -q` across the entire project (all 279+ tests must pass).

---

## 5. Security & Privacy Guardrails
- **Zero Secrets**: Master decryption key stays strictly inside process memory.
- **Zero Plaintext PII**: Comments and promo data remain encrypted in `promo_reviews.ciphertext` via AES-256 GCM.
- **Config-Driven**: No absolute paths hardcoded.
