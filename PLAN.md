# BAKKIS MUSIC SORTER v2 — rebuild plan

Status: milestones 1–6 done on `v2-pywebview` (plus a review pass); next: features below, then packaging. Written 2026-10-23 after a full review of v1 (`bakkis_music_sorter.py`, Tkinter, 8,634 lines).
Owner: Andreas (BAKKIS). Communicate with him in Norwegian; code, comments and commits in English.

## Goal

Rebuild the app with a modern UI (pywebview + HTML/CSS/JS) that is **fast and responsive**, keeps **full feature parity** with v1,
and can be **shared with other DJs and friends** as a double-clickable macOS app.

## Decisions (agreed with Andreas)

| Topic | Decision |
|---|---|
| UI toolkit | Leave Tkinter. **pywebview** (WKWebView on macOS) with an HTML/CSS/JS frontend, Python backend. |
| Scope v1 of the rebuild | **Full parity** with the old app (checklist below), redesigned UX. |
| File handling | **Move by default**, **Copy** as a setting. Warn once that moving breaks Rekordbox/Serato library links. |
| Platform | **macOS only.** |
| Playback | **Drop mpv.** Play audio in WebKit via `<audio>` served by a local HTTP server with Range support. Removes the mpv/dylib packaging problem and makes seeking instant. |
| Branching | Build on a new branch `v2-pywebview` off `main`. Keep v1 working until v2 reaches parity. |
| Language | **English everywhere**: UI, README, docs. No other languages for now. |
| Signing | Build script supports **both**: unsigned (default, free) and Developer ID signed + notarized (when an Apple Developer account exists). |
| Architecture | **Universal** app (Apple Silicon + Intel), built with python.org Python 3.12 universal2. Minimum **macOS 13 Ventura**. |
| Distribution | **GitHub Releases** (zip per version). App name "BAKKIS MUSIC SORTER", version 2.0.0, bundle id `com.bakkis.music-sorter`. |
| License | **Open source, MIT** (decided). The app doubles as a promo tool. BAKKIS name and logo excluded from the license. Audit git history for personal data before the repo goes public. |
| Promo & support | Discreet only, no popups: "Follow BAKKIS" music links + one support link (Buy Me a Coffee / Ko-fi suggested, plus GitHub Sponsors button) in README, Settings → About, and one line on the session end screen. Links open in the browser; the app sends no data. In the app: Spotify, SoundCloud, Instagram, Beatport, Traxsource, bakkis.com (done). **README links: bakkis.com (https://bakkis.com), Instagram (https://www.instagram.com/bakkismusic/) and all streaming links via songshare (https://songshare.com/artist/z7iy15wo).** Support: Ko-fi https://ko-fi.com/bakkis (in the app; also in README and a GitHub Sponsor button via .github/FUNDING.yml). |
| Contact | `bakkis@bakkis.com` in README, at the bottom of Settings (version + Contact), and a "Report a problem" button (prefilled e-mail with app + macOS version). |

## Problems found in v1 (fix or avoid in v2)

1. **UI freezes on every track change.** `_play_from_position` runs on the Tk main thread: `loadfile`, then a polling loop with `time.sleep` (up to 25 s in `_mpv_poll_until_decoder_ready`), then seek + retries. `shutil.move` also runs on the UI thread (slow on external disks).
2. **mpv packaging is broken for friends.** `build_mac_app.sh` copies only the Homebrew `mpv` binary; its dylibs are not bundled, so playback fails on Macs without Homebrew mpv.
3. **Stale build**: `dist/` zip is from 2026-05-11 (pre-mpv, pre-UI rewrite). `BAKKIS MUSIC SORTER.spec` is stale (old `/Users/andreasbakketeig/...` paths, ffmpeg).
4. **Personal paths in git**: `settings.json` / `bakkis_settings.json` contain `/Users/andreasbakketeig/Desktop/...` (user is now `bakkis`) and get bundled into the app.
5. **Undo can overwrite**: `undo_last` moves back without checking the original path is free. Undo history is memory-only (lost on quit).
6. **Undo shortcuts inconsistent**: tile says ⌘Z, binding is plain Z; ⌃Z undoes the whole session with no confirmation.
7. **Dead code**: `add_date_to_created_sorting_folders` setting + `genre_sort_folder_leaf`, `swipe_sort_folder_leaf`, `get_duration_seconds`, `_dj_button`, etc. are unused. Comments still mention afplay/ffmpeg.
8. **No tests.** `App` class is ~6,800 lines; settings dialog alone ~900 lines.
9. Open PR #1 on GitHub (Cursor-generated `AGENTS.md`) is unmerged.

## Architecture

```
bakkis_sorter/
  __init__.py      version, APP_NAME
  config.py        paths (bundle dir, user config dir), settings load/save/normalize/migrate, mode presets store + CRUD
  library.py       supported formats, scanning (thread, cancel, progress), skip-dir rules, BAKKIS_SORTED destination paths,
                   unique_destination, empty-source-folder cleanup chain
  metadata.py      mutagen: duration, title, artist, BPM, key, embedded cover bytes; LRU cache; prefetch thread pool
  session.py       queue (source of truth), sort/skip/undo/undo-all, single worker thread for file ops,
                   persistent journal (JSONL), copy-mode "already sorted" index, counters, notices
  server.py        ThreadingHTTPServer on 127.0.0.1:<random port> with a per-launch secret token:
                   GET / and /static/* (UI), GET /audio/<track_id> (Range support), GET /cover/<track_id>,
                   POST /api/<method> (JSON)
  app.py           pywebview window, native folder dialog, fullscreen, reveal in Finder, shutdown
  web/             index.html, app.css, app.js (no build step, no framework needed)
main.py            entry point
tests/             pytest for config, library, session (moves, copy, undo, journal, cleanup, collisions)
```

Why an HTTP API instead of only `js_api`: the same frontend runs in a normal browser against the server for fast UI development and
Playwright screenshots; in the app, pywebview just loads `http://127.0.0.1:<port>/?t=<token>`. Native-only calls (folder dialog,
fullscreen) go through the API and are executed by `app.py` via pywebview.

Security: bind to 127.0.0.1 only, require the token on every `/api`, `/audio`, `/cover` request, map track ids → paths
(never serve arbitrary paths).

## Performance requirements (Andreas: "fast and responsive is important")

- Sorting a track must show the next track **immediately** (optimistic UI). File ops run on a background worker; failures come back as a toast
  and the track is put back as *next* in the queue.
- Next track is **preloaded**: a second `<audio>` element with `preload="auto"` and `currentTime` set to the start point.
- Start position is set directly (`audio.currentTime = start`) — no poll/seek/retry loop.
- Metadata + cover for the next 5–8 tracks prefetched in a thread pool.
- Waveform: decode in the webview (`fetch` → `AudioContext.decodeAudioData`) after playback starts, compute ~800 peaks, cache peaks on disk
  in the user config dir keyed by path+size+mtime. Skip waveform for files > ~200 MB (show plain progress bar).
- Key repeat ignored for sort actions (`event.repeat`). Backend `sort` requires the current head `track_id` to avoid double sorts.
- No per-frame full re-renders; progress via `requestAnimationFrame` reading `audio.currentTime`.

## UX design (agreed direction)

Main screen (see mockup discussed in chat): one focused column.
- **Top bar**: app name · folder name · active preset (dropdown) · Swipe/Grid switch · thin progress bar · `124 / 812` · queue, settings, help icons.
- **Track card**: cover, title, artist, chips for BPM, key, length, source subfolder. Long titles scroll only when they overflow.
- **Waveform** as the scrubber: click anywhere to seek; markers for jump points 0:00/0:30/1:00/1:30/2:00/3:00/4:00/5:00/outro; active start marked.
- **Decision tiles**: Swipe mode = four big tiles (Archive / Skip / Favorite / Keep) with arrow glyph and live per-folder count.
  Grid mode = 3×3 tiles laid out like a numpad, colored per genre, with counts.
- **Footer**: short shortcut hints + toast "Moved to Keep · Undo".
- Queue, settings, guide are **drawers/overlays**, not permanent rails. Guide = `?`/H cheat-sheet overlay, shown on first launch.
- **Onboarding/empty state**: big "Choose music folder" + drag-and-drop a folder (pywebview exposes `pywebviewFullPath` on dropped files);
  one sentence on where files go (`BAKKIS_SORTED/…`) and that nothing is deleted.
- **Session end screen**: summary per folder, time spent, "Reveal in Finder", "Load another folder".
- Visual: dark theme, SF Pro (`-apple-system`), one accent (teal `#00D6B4` from v1), 8 px spacing grid, max 4 type sizes,
  color only on decision tiles and genre colors.

### Keyboard (v2)

| Key | Action |
|---|---|
| → | Keep (new default; setting `swipe_keep_direction` = right/left lets users keep v1 mapping ← Keep / → Archive) |
| ← | Archive |
| ↑ | Favorite or Archive (setting `swipe_archive_hotkey`, as v1) |
| ↓ | Skip |
| 1–8 (swipe) | Jump to 0:00, 0:30, 1:00, 1:30, 2:00, 3:00, 4:00, 5:00 (red flash if beyond track length) |
| 9 (swipe) | Outro (last 60 s) |
| 1–9 (grid) | Sort to grid slot |
| A / D | −30 s / +30 s |
| Space | Play/pause |
| R | Replay from active jump point |
| ⌘Z or Z | Undo last |
| ⇧⌘Z or ⌃Z | Undo all — **with confirmation** |
| S or T / G | Swipe mode / Grid mode |
| U / O | Random order / In order (reorder queue, keep current track) |
| Q | Queue drawer · H or ? Guide · Tab Focus mode (hide chrome) · ⌘F Fullscreen · Esc exit fullscreen/close overlay |

Gestures (swipe mode): drag the track card in a direction (overlay shows target; threshold ~38 px), trackpad two-finger horizontal flick.

## Full parity checklist (from v1)

Folder & scanning
- [x] Choose folder (native dialog) and drag-and-drop folder
- [x] Default folder: set / clear / load; auto-load on startup; message if missing
- [x] Include subfolders (setting); skip hidden files/dirs, `.DS_Store`, `._*`; skip `BAKKIS_SORTED`, legacy KEEP/ARCHIVE/FAVORITES/SKIPPED/GENRES, and preset folder names
- [x] Supported formats: mp3, wav, aiff, aif, flac, m4a, aac, alac, ogg (playback error → notice + auto-skip with undo, as v1)
- [x] Background scan with progress; cancel when a new folder is loaded
- [x] Track order: random / in order (U/O + setting); reorder preserves current track

Sorting
- [x] Output tree `BAKKIS_SORTED/<preset>/[YYYY-MM-DD/]<target>/…` ("Create dated sessions" setting)
- [x] Swipe: Keep / Archive / Favorite (labels, colors, folder names from active swipe preset)
- [x] Grid: 9 slots (labels, colors, folders from active grid preset)
- [x] Skip → `…/Skipped/` preserving relative subfolders
- [x] Name collisions → `name (2).ext`
- [x] Clean up empty source folders after sort (setting; ignores `.DS_Store`, `Thumbs.db`, `desktop.ini`, `._*`); undo recreates them
- [x] **New**: Copy mode; in copy mode remember sorted sources (`BAKKIS_SORTED/.bakkis_copied.json`) and hide them on rescan (setting)
- [x] Undo last / undo all; undo never overwrites (unique name + notice); undo of a copy deletes only the copy we made
- [x] **New**: persistent journal `~/Library/Application Support/BAKKIS MUSIC SORTER/journal/YYYY-MM-DD.jsonl`
- [x] Soft click sound on sort/skip (setting) — synthesize in WebAudio, no file
- [x] When a track plays to the end: auto-advance as Skip (v1 behavior); **new** setting: stop instead

Playback
- [x] Default start time (0:00, 0:30, 1:00, 1:30, 2:00, 3:00); if duration < start + 30 s start at 0:00
- [x] Jump keys/markers, outro, ±30 s, replay (R), play/pause, volume (persisted)
- [x] Now playing: title — artist (tags, else "Artist - Title" filename, else filename), BPM, key, duration, cover art

Presets & settings
- [x] Mode presets store `mode_presets.json` (same format as v1): swipe presets (3 labels/colors/folders) and grid presets (9)
- [x] New / edit / delete / select preset per mode; startup preset; reset grid to default genres
- [x] Genre palette (Teal, Blue, Purple, Gray, Orange, Pink, Red, Yellow, Dark)
- [x] Settings: default folder, auto-load, default mode on startup, startup preset, default start time, default order,
      include subfolders, click sound, ↑ target, dated sessions, cleanup empty folders, show guide on startup,
      show queue on startup, volume; **new**: move/copy, keep direction, end-of-track behavior
- [x] Read v1 `settings.json` / `bakkis_settings.json` / `mode_presets.json` / `genre_labels.json` (migrate; drop dead keys)

Panels & stats
- [x] Queue drawer: upcoming tracks (virtualized) + last 5 actions; click a track to play it next (new)
- [x] Session stats (per target, total, elapsed) and per-folder counts on tiles
- [x] Guide / shortcut overlay; focus mode (Tab); fullscreen (⌘F / Esc)


Parity pass done 2026-10-23 (all items implemented and covered by tests or checks in the real window). Deliberate
differences from v1: preset/genre folder names no longer hide source folders from the scan (only v1's flat output
folders at the top level are skipped); start times go up to 6:00 and a too-short track starts about a third in
instead of at 0:00; swipe default is → Keep (v1: ← Keep), switchable in Settings. v1's live session line is shown in
the footer. Still to be checked by hand on Andreas' Mac: picking a folder in the native dialog, a real drag and drop
from Finder, a real two-finger flick, and a first launch on a Mac/user account without his settings.

## Settings & data locations

- Always use `~/Library/Application Support/BAKKIS MUSIC SORTER/` (also in dev), overridable with env `BAKKIS_CONFIG_DIR` (tests).
- On first run, migrate from v1 locations (project folder JSON files) if present.
- Remove personal settings files from git; ship defaults in code (and optional `defaults/` folder).

## Packaging (macOS)

- PyInstaller `--windowed`, bundle id `com.bakkis.music-sorter`, include `bakkis_sorter/web/` and `bakkislogo.png`; pywebview uses the system WebKit, so no extra binaries.
- Proper `.icns` icon from the logo.
- New `build_mac_app.sh`; delete the stale `.spec` (or regenerate it).
- Distribute as zip; document right-click → Open for unsigned apps. Optional later: Developer ID signing + notarization.
- Rewrite `PACKAGING.md` and add a `README.md` (English only: user section, install incl. Gatekeeper steps, contact, license/brand note, dev section).

## Repo hygiene

- `.gitignore`: add `settings.json`, `bakkis_settings.json` if they stay user-specific; keep `dist/`, `build/` ignored.
- Remove `_bakkis_sort_click.wav` from git (sound is synthesized in v2).
- Decide on PR #1 (`AGENTS.md`) — merge or close; this file + `CLAUDE.md` supersede it.

## Features added after the review (all approved by Andreas, 2026-10-23)

1. **Export playlists for Rekordbox / Serato**: M3U8 per destination folder (and Rekordbox XML) after or during a session.
2. **History across sessions**: a view over the journal with "put back" for past sessions; never overwrites.
3. **Filter / order the queue by BPM or key** (BPM range, Camelot order).
4. **Report a problem**: prefilled e-mail to bakkis@bakkis.com with version, macOS and a recent log excerpt.
5. **Update check**: on startup, compare with the latest GitHub Release and show a notice (no auto-update; can be turned off; documented in README since it uses the network).
6. **Duplicate warning**: flag tracks that already exist in BAKKIS_SORTED (title + artist + duration).
7. **Search the queue** by name in the queue drawer.

## Milestones

1. Branch `v2-pywebview`; scaffold package; `config.py` + `library.py` + tests.
2. `session.py` (worker, journal, undo, copy mode) + tests incl. collisions, cross-dir cleanup, failed moves.
3. `metadata.py` + `server.py` (Range requests, token) + tests.
4. Frontend: main screen, playback, preload, waveform, swipe + grid, keyboard, toasts. Measure: sort → next track audible < 150 ms.
5. Drawers/overlays: queue, settings, preset/genre editors, guide, onboarding, session end.
6. `app.py` (pywebview), folder dialog, drag-and-drop, fullscreen, reveal in Finder.
7. Features 1–7 above (approved after the review pass).
7b. Packaging (universal, unsigned + signed/notarized paths) + README/PACKAGING; test on a clean user account without Homebrew.
8. Parity pass against the checklist; then merge to `main` and tag `v2.0.0`.

## Testing

- `pytest` for backend modules (use temp dirs; never touch real music).
- Run the server alone (`python -m bakkis_sorter.server --dev <folder>`) and test the UI in a browser / Playwright.
- Manual test on macOS with a test folder of mixed formats (mp3, wav, aiff, flac, m4a, ogg), long titles, missing tags, nested subfolders, an external disk.
