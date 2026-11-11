# BAKKIS MUSIC SORTER

**Fast. Instinctive. Sorting & Promo Response.**

*Clear the backlog. Keep the bangers. Respond in seconds.*

A free macOS app for DJs and producers with two focused workflows:

1. **Sorting & Organizing Local Music**: Sort massive crates, downloads, and unorganized folders one track at a time. Listen from the best part, decide with a single key or swipe, and file the music away instantly while the next track plays.
2. **Responding to Music Promos**: A unified promo reviewer combining **Inflyte Promo Box** and **Trackstack** into one fast queue. Preview releases, send automated ratings and comments with a single keystroke, download full-res tracks directly to your Downloads folder, and queue feedback offline for flights.

Made by [BAKKIS](https://bakkis.com), DJ and producer from Norway.

> *My music folders were huge and messy, and sorting them meant endless clicking and dragging. So I built the app I wanted: listen, swipe, next. No mouse needed.* (BAKKIS)

*Promo review integration, cross-platform queueing, and v3.0 performance enhancements were conceived and developed in collaboration with DJ and producer [Ed Hoffman](https://www.instagram.com/edhoffman_music/) ([@edhoffman_music](https://www.instagram.com/edhoffman_music/)), who needed a lightning-fast way to eliminate promo backlogs and respond to label promotions without clicking through endless websites.*

- **Two Core Modes**: Swipe mode (Keep, Archive, Favorite, Skip) & Grid mode (sort into up to 9 genre crates)
- **Unified Promo Box**: Review Inflyte & Trackstack campaigns in one combined stream
- **Offline Travel Stash**: Stash promos to your Mac for offline reviewing on airplanes; syncs reviews back when reconnected
- **Duplicate Campaign Shield**: Fingerprints tracks across different promoter mailouts and badges tracks you already reviewed
- **Clean Downloads**: Full-res promo downloads saved straight to your `~/Downloads` folder (or custom folder) with zero file moving
- **High-Performance Audio**: Apple Silicon `libsonare` engine for real-time Camelot key, BPM, and drop detection
- **Lossless Undo**: Undo anything, including whole past sessions
- **File Safety**: No track is ever deleted or overwritten

---

## Install

**Requirements:** macOS 13 Ventura or later, Apple Silicon or Intel.

1. Download `BAKKIS-MUSIC-SORTER-x.y.z.dmg` from [bakkis.com/tools](https://bakkis.com/tools) or the [Releases page](https://github.com/andreasbakketeig-hub/BAKKIS-MUSIC-SORTER/releases).
2. Open the DMG and drag **BAKKIS MUSIC SORTER** onto the **Applications** folder in the window. (Open it from Applications, not from the DMG: only then can it update itself.)
3. Open it once as described below. You only need to do this the first time. You can then eject the DMG and delete it.

### First launch of an unsigned app

The app is not signed with an Apple Developer ID yet, so macOS asks you to confirm that you trust it.

**macOS 15 Sequoia and later**

1. Double-click **BAKKIS MUSIC SORTER** in Applications. macOS says it could not verify the app. Click **Done**.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to **Security**. Next to *"BAKKIS MUSIC SORTER" was blocked to protect your Mac*, click **Open Anyway**.
4. Enter your Mac password, then click **Open Anyway** once more.

**macOS 13 Ventura and macOS 14 Sonoma**

1. In Applications, **right-click** (or Control-click) **BAKKIS MUSIC SORTER** and choose **Open**.
2. Click **Open** in the dialog.

After that the app opens normally with a double-click.

**If macOS says the app "is damaged and can't be opened"**, the download was quarantined. Open Terminal and run:

```bash
xattr -dr com.apple.quarantine "/Applications/BAKKIS MUSIC SORTER.app"
```

Then open the app again.

### Updating

When a new version is out, the app tells you at startup. Click **Update now**: it downloads the new version, checks it against the checksum GitHub publishes, swaps it in and restarts. Your settings, presets and history stay, and you don't need to approve the app again in System Settings. (The app has to be in your Applications folder, or another folder you can write to; otherwise it offers the download link instead.)

---

## Getting started

BAKKIS MUSIC SORTER provides two distinct, focused workflows depending on what you are tackling:

| Workflow | Best For | Input Source | File Action | Primary Shortcuts |
|---|---|---|---|---|
| **Workflow 1: Local Sorter** *(Start Here)* | Organizing messy folders, downloads, and hard drive archives | Any local folder or drive on your Mac | Moves or copies into sorted folders (`BAKKIS_SORTED`) | **→** Keep · **←** Archive · **↑** Favorite · **↓** Skip · **1–9** Grid Crates |
| **Workflow 2: Promo Reviewer** | Clearing promo backlogs from record labels & PR agencies | Inflyte Promo Box & Trackstack accounts | Direct high-res download to `~/Downloads` (files never moved) | **↑** 5★ Favorite · **→** 4★ Keep · **←** Decline · **↓** Next Track |

---

### Workflow 1: Local Music Sorter (Start Here)

> **Before your first session:** back up your music (Time Machine or a copy on another disk), or try the app on a copy of one folder first. If the tracks are in a Rekordbox or Serato library, choose **Copy** when the app asks, so the originals stay where your library expects them.

1. **Choose a music folder**: on the Welcome Hub, click **Choose Music Folder** under *Workflow 1: Sorter*, or drag a folder (or a track inside it) anywhere into the window. Subfolders are included automatically.
2. **Instant playback**: the first track immediately starts playing at the drop or jump point (default: **2:00**; customize between 0:00 and 6:00 in Settings → Playback).
3. **Decide with one key**:
   - **Swipe mode**: **→** Keep, **←** Archive, **↑** Favorite, **↓** Skip (or flick on trackpad).
   - **Grid mode**: Press **1–9** to file into up to 9 custom genre crates.
   - The file is sorted instantly in the background while the next track plays immediately with zero delay.

The first time you sort, the app asks whether to **move** or **copy** files:
- **Move** (default) keeps your hard drive clean and removes empty folders left behind. *(Warning: moving files alters their file path, which will break existing Rekordbox or Serato library links).*
- **Copy** leaves your originals untouched and writes copies into a separate `BAKKIS_SORTED_COPIES` directory, so you can safely experiment without modifying your primary collection.

#### Where your files go

Moved tracks go into a `BAKKIS_SORTED` folder inside the folder you chose (copies into `BAKKIS_SORTED_COPIES`):

```
Your Folder/
└── BAKKIS_SORTED/
    └── Quick Sort/            ← the preset you sort with
        ├── Keep/
        ├── Archive/
        ├── Favorite/
        └── Skipped/           ← keeps your original subfolders
```

- **Zero Overwriting**: If a track with the same filename already exists, the new copy is saved as `name (2).mp3`.
- **Tidy Folders**: Source directories that become empty after moving tracks are automatically cleaned up.
- **Lossless Undo**: Press **Z** (or **⌘Z**) at any time to move the track back to its exact original location.

---

### Workflow 2: Unified Promo Review (Inflyte & Trackstack)

DJs receiving dozens of weekly promos across different platforms can clear their entire inbox in minutes without clicking through slow web interfaces:

1. **Connect your accounts**: on the Welcome Hub under *Workflow 2: Promos*, click **Inflyte · Log In** and **Trackstack · Log In**. Authenticate once in the secure WKWebView window; credentials and session tokens are encrypted in your macOS Keychain.
2. **Configure your feedback**: click **💬 Configure comments & ratings** to customize your rating numbers (e.g. 5★ for favorites, 4★ for keeps) and comment pools. BAKKIS rotates through authentic comments so your promoter replies remain varied and natural.
3. **Review with one keystroke**: click **⚡ Review All Promos**.
   - **↑** (Feeling this / Favorite): Submits your 5★ rating + cycles a personal comment, triggers cloud sync, and downloads the full-res file.
   - **→** (Keep / Maybe): Submits 4★ rating + comment, triggers cloud sync, and downloads the track.
   - **←** (Not for me / Decline): Dismisses the promo from your inbox or declines on Trackstack.
   - **↓** (Skip / Next Track): Steps through tracks in multi-track releases.
4. **Offline flight mode**: traveling without Wi-Fi? Click **✈️ Stash Promos for Travel** to pre-download waveforms and preview audio. Review while offline, and click **Sync Stashed Reviews** once you land.
5. **Zero file movement on disk**: all promo downloads save cleanly to your native `~/Downloads` folder. BAKKIS never moves or renames promo files; tracking and ratings remain strictly in SQLite (`bakkis.db`).

---

### Example workflow: genre first, then Quick Sort

1. Load your big downloads folder and sort it with **Genre Sort** (grid mode, keys 1–9).
2. Load one of the genre folders, for example `BAKKIS_SORTED/Genre Sort/Techno`, and sort it with **Quick Sort**.
3. The result lands right inside that genre folder: `Genre Sort/Techno/Quick Sort/Keep/`. Loading the genre folder again only shows the tracks you have not sorted yet.

Inside `BAKKIS_SORTED_COPIES` the tracks are already copies, so a second pass moves them instead of copying them again. Your originals are never touched.

---

## Sorting

### Swipe mode

| Action | Keys and gestures |
|---|---|
| Keep | **→**, drag the card right, or flick right with two fingers |
| Archive | **←**, drag left, or flick left |
| Favorite | **↑**, drag up, or flick up (the ↑ key can sort to Archive instead, see Settings) |
| Skip | **↓**, drag down, or flick down |

Prefer Keep on the left? Change **Settings → Sorting → Swipe direction**. The trackpad flick follows your fingers whether macOS "natural scrolling" is on or off.

### Grid mode

Press **1–9** to sort into one of nine tiles, laid out 1-2-3 on the top row. The default tiles are House, Tech House, Deep House, Minimal, Afro House, Melodic, Techno, Garage and Other. Edit them in Settings → Presets.

Prefer a calmer screen? Turn off **Settings → Advanced → Colored tiles**: the colour wash goes and only a thin stripe on top remains.

### Presets

A preset is a set of tiles with labels, colors and folder names. Switch between them from the menu in the top bar; each preset gets its own folder in `BAKKIS_SORTED`. The app comes with:

| Preset | Mode | Tiles |
|---|---|---|
| **Quick Sort** | Swipe | → Keep · ← Archive · ↑ Favorite |
| **Promos** (for DJs answering label promos) | Swipe | → Maybe · ← Not for me · ↑ Feeling it |
| **Demos** (for labels answering demo submissions) | Swipe | → Hold · ← Reject · ↑ Sign |
| **Genre Sort** | Grid | House, Tech House, Deep House, Minimal, Afro House, Melodic, Techno, Garage, Other |
| **Set Builder** | Grid | Warm-up, Deep, Groove, Build-up, Peak time, Driving, Closing, After hours, Other |

Edit them, delete them or create your own in **Settings → Presets**. A deleted built-in preset does not come back.

### All shortcuts

| Key | Action |
|---|---|
| ← → ↑ ↓ | Sort (swipe mode) / ↓ skip (both modes) |
| 1–8 | Jump to 0:00, 0:30, 1:00, 1:30, 2:00, 3:00, 4:00, 5:00 (swipe mode) |
| 9 | Outro: the last 60 seconds (swipe mode) |
| 1–9 | Sort to that tile (grid mode) |
| Space | Play / pause |
| A / D | Back / forward 30 seconds |
| R | Replay from the jump point |
| Z or ⌘Z | Undo the last action |
| ⇧⌘Z or ⌃Z | Undo the whole session (asks first) |
| S / G | Swipe mode / Grid mode |
| U / O | Random order / In order (the current track keeps playing) |
| Q | Queue |
| H or ? | Shortcut guide |
| Tab | Focus mode (hides everything but the track) |
| L | Discover |
| ⌘F | Full screen |
| Esc | Close a panel / leave full screen |

Click anywhere on the waveform to jump there.

### Undo and history

- **Z** undoes the last action, as many times as you like.
- The **History** panel (clock icon) lists everything you sorted in the last 90 days that is still in place. Put back single tracks or whole sessions. In copy mode, putting back removes only the copy.
- A log of every action is kept in `~/Library/Application Support/BAKKIS MUSIC SORTER/journal/`.

### Copy feedback (promo reports and demo replies)

**Copy feedback** puts a plain-text summary of your decisions on the clipboard, ready to paste into a promo report, an e-mail or a chat:

```
Promos – 23 Oct 2026

Feeling it (2)
• Jimpster – Sleeper
• Solomun – Home (Club Mix)

Maybe (1)
• John Tejada – Steppa

Not for me (1)
• …
```

It is on the end screen, in the queue panel (*Copy feedback so far*, for when you stop halfway) and next to every past session in History. The best verdict comes first, skips and undone tracks are left out, and nothing is sent anywhere.

### Good to know

- **Unplayable tracks** (a broken file, or a format your Mac can't play) are skipped automatically. Undo brings them back.
- **When a track ends**, it is skipped and the next one plays (or the app stops, see Settings). After three tracks end in a row without you touching anything, the app pauses, so it never empties a whole folder on its own.
- **Already sorted?** If the same track (title, artist and length) is already somewhere in `BAKKIS_SORTED` or `BAKKIS_SORTED_COPIES`, the card shows *Already in …*.
- Titles are cleaned up for display (download-site stamps like `myfreemp3.vip`, `%28Original Mix%29`, key/BPM prefixes). Your files and tags are never changed.

### Hands-free with macOS Voice Control

Everything in the app works from the keyboard, so macOS **Voice Control** can drive it without any extra setup:

1. Open **System Settings → Accessibility → Voice Control** and turn it on.
2. With the app in front, say for example *"Press right arrow"* (Keep), *"Press left arrow"* (Archive) or *"Press Z"* (undo).
3. For shorter commands, click **Commands…**, then **+**. Set *When I say* to "Keep", *While using* to BAKKIS MUSIC SORTER, and *Perform* to **Press Keyboard Shortcut** → **→**. Do the same for "Archive" (←), "Skip" (↓), "Favorite" (↑) and "Undo" (Z).

Use headphones: with speakers the microphone also hears the music, and vocals can be mistaken for commands. Anything sorted by mistake is one **Z** away from coming back.

---

## Responding to music promotion (Inflyte & Trackstack)

DJs receiving dozens of weekly promos across different platforms can clear their entire inbox in minutes instead of hours. BAKKIS connects directly to **Inflyte Promo Box** and **Trackstack**, merging unreviewed mailouts into one fast, unified review flow.

### Connecting your accounts

On the Welcome Hub, you'll find live status indicators for both platforms:
- **Inflyte**: Click **Inflyte · Log In** once. A secure WKWebView window opens to `inflyteapp.com`; log in as normal. Once authenticated, your session is remembered securely in macOS Keychain encrypted storage (`bakkis.db`), so you stay connected across restarts.
- **Trackstack**: Click **Trackstack · Log In**. Sign in once on `trackstack.app`. Your workspace and inbox are discovered automatically.

Click **⚡ Load Promos** to start reviewing!

### Promo decisions & feedback

When reviewing promos, the app automatically maps your keystrokes to platform feedback:

| Keystroke / Gesture | Inflyte Action | Trackstack Action |
|---|---|---|
| **↑** (Feeling this / Favorite) | Submits your configured 5-star rating + cycles a personalized comment; triggers Dropbox cloud sync | Moves track to your Trackstack shortlist/downloaded stack; downloads full-quality file to `~/Downloads` |
| **→** (Keep / Maybe) | Submits positive 4-star rating + comment; triggers Dropbox cloud sync | Moves track to shortlist/downloaded stack; downloads full-quality file to `~/Downloads` |
| **←** (Not for me / Decline) | Dismisses the promo from your Inflyte inbox | Declines the submission on Trackstack |
| **↓** (Skip / Next Track) | Advances to the next track in the promo; on the final track, sets the promo aside for later | Advances to the next track or sets aside for later |

### In-app comments pool & ratings

Promoters appreciate authentic, varied feedback. Click **💬 Feedback Comments** on the Welcome Hub to customize:
- Numeric star rating for *Feeling this* (default: 5/5) and *Keep / Maybe* (default: 4/5)
- Separate comment pools for 5-star and 4-star ratings (e.g., *"Absolute weapon, playing this weekend!"*, *"Solid groove, will test this out"*). The app cycles through comments randomly so you never sound repetitive.
- **Download Location**: Defaults to your native macOS `~/Downloads` folder. You can change it to any folder on your Mac with the **Choose…** button.

### Cross-campaign duplicate detection

Promoters and PR agencies frequently send the same release under different campaigns or subject lines. BAKKIS generates a canonical track signature (`artist:title`) for every track. If a song was already reviewed in a past campaign, the card displays an **"Already Reviewed: 5/5 on [Promo Name]"** badge so you never waste time re-listening or send conflicting ratings.

### Offline Stash (flight mode)

Traveling without internet? Click **✈️ Stash Promos for Travel** before heading to the airport.
- BAKKIS pre-downloads audio previews and cover art to your local cache.
- Review your entire promo queue offline with full keyboard shortcuts and waveforms.
- When you reconnect to Wi-Fi, click **Sync Stashed Reviews**: all ratings, comments, and decisions are instantly dispatched to Inflyte and Trackstack in the background.

### Zero file movement on disk

Your local files are sacred:
- Downloads from Trackstack stream directly into `~/Downloads` (or your chosen download folder) and **stay there permanently**.
- Inflyte syncs files directly into your connected folder.
- BAKKIS **never moves, renames, or alters** your downloaded promo files. All review status, duplicate checks, and ratings are tracked 100% inside encrypted SQLite (`bakkis.db`).

---

## Game controller

Sort from the sofa: connect a game controller with USB or Bluetooth, press any button, and it works like the keyboard. It rumbles on every sort, so you can feel each decision.

| Button (PlayStation / Xbox) | Does |
|---|---|
| D-pad or left stick | Sort, like the arrow keys (→ Keep, ← Archive, ↑ Favorite, ↓ Skip) |
| ✕ / A | Play / pause (or confirm in a dialog) |
| ○ / B | Undo (or close a dialog or panel) |
| L1 R1 / LB RB | −30 s / +30 s |
| Options / ☰ | Shortcuts |

Works with controllers macOS supports: PlayStation 4 and 5, Xbox One and Series, Nintendo Switch Pro and most 8BitDo models. It only reacts while the app's window is active, so a controller used for a game never sorts anything. Turn it off in **Settings → Advanced → Controls**.

To pair a PS5 controller: hold **Create** and the **PS** button until the light bar flashes, then pick it in **System Settings → Bluetooth**.

## More features

These are on by default. Switch them off under **Settings → Advanced → Features** if you don't use them:

- **Playlist export for Rekordbox and Serato**: one M3U8 playlist per sorted folder, written to `_Playlists/` inside `BAKKIS_SORTED` (and `BAKKIS_SORTED_COPIES`). In Rekordbox use *File → Import → Playlist*; in Serato, drag the files onto the crate list.
- **Discover** (press **L** or choose *Discover* in the top bar): listen through your sorted folders and/or the unsorted tracks without sorting anything, as full tracks or as 30–90 second snippets from the middle of each track with a crossfade. **→ / ←** next and previous, **↑** or **F** hearts a track (press again to remove the heart). Your hearted list is kept until you use it: copy the tracks into a folder in `BAKKIS_SORTED_COPIES/Discover/` (the originals stay where they are) or export it as a playlist. Nothing is moved while you listen.
- **Filter the queue by BPM and key**: show only, for example, 120–124 BPM in 8A (optionally with harmonically compatible keys), ordered by BPM or key. Other tracks are set aside, not moved, and come back when you clear the filter. Keys are read as Camelot (8A), musical (Am) or Open Key (1m).

---

## Privacy

- The app works entirely on your Mac. It never uploads your music, file names or listening data.
- The only network request is an optional check for a new version on GitHub at startup (Settings → Advanced → Startup → *Check for new versions*). Nothing is downloaded unless you click **Update now**.
- Links (Follow BAKKIS, Support, Report a problem) open in your browser or mail app.

## Settings and uninstalling

Settings, presets, history and waveform cache are stored in:

```
~/Library/Application Support/BAKKIS MUSIC SORTER/
```

To uninstall, delete the app from Applications and, if you like, that folder. Your sorted music stays where it is.

---

## Troubleshooting

- **The app won't open**: see [First launch of an unsigned app](#first-launch-of-an-unsigned-app).
- **A track won't play**: it is skipped automatically. Press **Z** to bring it back if you want to sort it anyway.
- **Music on an external disk**: works, but moving files between disks takes longer. The app stays responsive while files are moved in the background.
- **Anything else**: use **Settings → About → Report a problem**. It opens an e-mail with your app and macOS version filled in.

---

## Contact and support

- E-mail: [bakkis@bakkis.com](mailto:bakkis@bakkis.com)
- If the app saves you time, you can [buy me a coffee on Ko-fi](https://ko-fi.com/bakkis) ☕

**Follow BAKKIS**

- Website: [bakkis.com](https://bakkis.com)
- Instagram: [@bakkismusic](https://www.instagram.com/bakkismusic/)
- All streaming links: [songshare.com/artist/z7iy15wo](https://songshare.com/artist/z7iy15wo)

---

## Disclaimer

BAKKIS MUSIC SORTER moves or copies the files you tell it to. It is careful by design: no track is ever deleted (only source folders that became empty), existing files are never overwritten, and every action can be undone. Still, it is free software provided **as is, without warranty of any kind** (see the [license](LICENSE)). You use it at your own risk; keep a backup of music you care about.

## License

The code is free and open source under the [MIT License](LICENSE).

The **BAKKIS** name and logo are not covered by the license and may not be used in other projects or derived apps without permission.

---

## For developers

The app is a Python backend with a plain HTML/CSS/JS frontend shown in a native macOS window ([pywebview](https://pywebview.flowrl.com/), WKWebView). Audio plays in WebKit from a local HTTP server bound to `127.0.0.1`, protected by a per-launch token.

### Run from source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python main.py
```

Or double-click `Start BAKKIS v2.command` (it quits a running copy and starts the newest code).

Set `BAKKIS_DEBUG=1` to enable the Web Inspector. Set `BAKKIS_CONFIG_DIR=/some/folder` to use a separate settings folder, for example for testing.

**Browser dev mode** (UI work without the app window):

```bash
.venv/bin/python -m bakkis_sorter.server --dev "/path/to/a/TEST/folder"
```

It prints a URL to open in a browser.

### Tests

```bash
.venv/bin/python -m pytest
```

The tests only use temporary folders. Never point tests or dev mode at a music folder you care about. Use a copy.

### Layout

```
bakkis_sorter/
  promos/        Inflyte & Trackstack bridges, tRPC client, offline stash, multi-track session
  session/       local queue, sort/skip/undo, worker, copy index, journal
  storage/       macOS Keychain AES-256 SQLite database (bakkis.db), reviews, signatures, peaks
  metadata/      ID3 tags, cover downsampler, ByteBoundedLRU, content hashes
  config/        settings, presets, coercions, atomic persistence
  backend/       API mixins (promos, library, settings, diagnostics)
  library.py     scanning, destination paths, empty-folder cleanup
  duplicates.py  cross-campaign track signatures and "already sorted" index
  analyzer.py    libsonare integration (BPM, Camelot key, drops)
  logger.py      structured SQLite logger
  server.py      local HTTP server (UI, audio streaming with Range requests, JSON API)
  app.py         pywebview window, folder dialog, native drag-and-drop
  web/           HTML5 UI, domain modular CSS (web/css/), ES modules (web/drawers/)
main.py          entry point
tests/           pytest suite (284 unit & integration tests)
```

The original v1 (Tkinter) is kept for reference in `legacy/`; run it with `legacy/Start BAKKIS Music Sorter.command`. It is no longer developed.

The app icon is drawn by `tools/make_icon.py` (writes `assets/icon/icon-1024.png` and `AppIcon.icns`).

Building the `.app` is described in [PACKAGING.md](PACKAGING.md).
