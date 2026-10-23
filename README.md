# BAKKIS MUSIC SORTER

**Fast. Instinctive. Sorting.**

A free macOS app for DJs to sort big folders of promos and downloads, one track at a time. Listen from the best part of the track, decide with one key (or a swipe), and the file is filed away instantly while the next track is already playing.

Made by [BAKKIS](https://bakkis.com), DJ and producer from Norway.

- Swipe mode: Keep, Archive, Favorite or Skip with the arrow keys, a mouse drag or a two-finger trackpad flick
- Grid mode: sort into up to nine genres with the number keys
- Plays MP3, WAV, AIFF, FLAC, ALAC, AAC/M4A (and Ogg where macOS supports it)
- Undo anything, including whole past sessions
- Nothing is ever deleted

---

## Install

**Requirements:** macOS 13 Ventura or later, Apple Silicon or Intel.

1. Download the latest `BAKKIS-MUSIC-SORTER-x.y.z.zip` from the [Releases page](https://github.com/andreasbakketeig-hub/BAKKIS-MUSIC-SORTER/releases).
2. Open the zip (Safari usually unzips it for you) and drag **BAKKIS MUSIC SORTER** into your **Applications** folder.
3. Open it once as described below. You only need to do this the first time.

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

---

## Getting started

1. **Choose a music folder**: click **Choose music folder**, or drag a folder (or a track inside it) into the window. Subfolders are included.
2. The first track starts playing at **2:00**. Choose anything from 0:00 to 6:00 in Settings → Playback; a track too short for that starts about a third of the way in.
3. **Decide.** The track is filed and the next one plays immediately.

The first time you sort, the app asks whether to **move** or **copy** files:

- **Move** (default) keeps your folders tidy, but if the tracks are in a **Rekordbox or Serato library, their links will break**.
- **Copy** leaves the originals where they are and puts copies in the sorted folders. Copied tracks are hidden the next time you load the folder.

A short guide shows the shortcuts on first launch. Press **H** or **?** at any time to see it again.

### Where your files go

Everything goes into a `BAKKIS_SORTED` folder inside the folder you chose:

```
Your Folder/
└── BAKKIS_SORTED/
    └── Quick Sort/            ← the preset you sort with
        └── 2026-10-23/        ← one folder per day (optional)
            ├── Keep/
            ├── Archive/
            ├── Favorite/
            └── Skipped/       ← keeps your original subfolders
```

- **Nothing is deleted.** If a file with the same name already exists, the new one is saved as `name (2).mp3`.
- Undo puts the file back where it came from, and never overwrites anything there.
- Optionally, source folders that become empty can be removed (Settings → Sorting). Undo recreates them.

---

## Sorting

### Swipe mode

| Action | Keys and gestures |
|---|---|
| Keep | **→**, drag the card right, or flick right with two fingers |
| Archive | **←**, drag left, or flick left |
| Favorite | **↑** or drag up (↑ can sort to Archive instead, see Settings) |
| Skip | **↓** or drag down |

Prefer Keep on the left? Change **Settings → Sorting → Keep is**. The trackpad flick follows your fingers whether macOS "natural scrolling" is on or off.

### Grid mode

Press **1–9** to sort into one of nine tiles, laid out 1-2-3 on the top row. The default tiles are House, Tech House, Deep House, Minimal, Afro House, Melodic, Techno, Garage and Other. Edit them in Settings → Presets.

### Presets

A preset is a set of tiles with labels, colors and folder names, for example *Quick Sort* (swipe) or *Genre Sort* (grid). Create as many as you like in **Settings → Presets**, and switch between them from the menu in the top bar. Each preset gets its own folder in `BAKKIS_SORTED`.

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
| L | Discover (advanced features) |
| ⌘F | Full screen |
| Esc | Close a panel / leave full screen |

Click anywhere on the waveform to jump there.

### Undo and history

- **Z** undoes the last action, as many times as you like.
- The **History** panel (clock icon) lists everything you sorted in the last 90 days that is still in place. Put back single tracks or whole sessions. In copy mode, putting back removes only the copy.
- A log of every action is kept in `~/Library/Application Support/BAKKIS MUSIC SORTER/journal/`.

### Good to know

- **Unplayable tracks** (a broken file, or a format your Mac can't play) are skipped automatically. Undo brings them back.
- **When a track ends**, it is skipped and the next one plays (or the app stops, see Settings). After three tracks end in a row without you touching anything, the app pauses, so it never empties a whole folder on its own.
- **Already sorted?** If the same track (title, artist and length) is already somewhere in `BAKKIS_SORTED`, the card shows *Already in …*.
- Titles are cleaned up for display (download-site stamps like `myfreemp3.vip`, `%28Original Mix%29`, key/BPM prefixes). Your files and tags are never changed.

---

## Advanced features

Turn on **Settings → Advanced → Show advanced features** to get:

- **Playlist export for Rekordbox and Serato**: one M3U8 playlist per sorted folder, written to `BAKKIS_SORTED/_Playlists/`. In Rekordbox use *File → Import → Playlist*; in Serato, drag the files onto the crate list.
- **Discover** (press **L** or choose *Discover* in the top bar): listen through your sorted folders and/or the unsorted tracks without sorting anything, as full tracks or as 30–90 second snippets from the middle of each track with a crossfade. **→ / ←** next and previous, **↑** or **F** hearts a track (press again to remove the heart). Your hearted list is kept until you use it: copy the tracks into a folder in `BAKKIS_SORTED/Discover/` (the originals stay where they are) or export it as a playlist. Nothing is moved while you listen.
- **Filter the queue by BPM and key**: show only, for example, 120–124 BPM in 8A (optionally with harmonically compatible keys), ordered by BPM or key. Other tracks are set aside, not moved, and come back when you clear the filter. Keys are read as Camelot (8A), musical (Am) or Open Key (1m).

---

## Privacy

- The app works entirely on your Mac. It never uploads your music, file names or listening data.
- The only network request is an optional check for a new version on GitHub at startup (Settings → Startup → *Check for new versions*). Nothing is downloaded automatically.
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
  config.py      settings, presets (mode_presets.json), v1 migration
  library.py     scanning, destination paths, empty-folder cleanup
  session.py     queue, sort/skip/undo, background file worker, journal, copy mode
  metadata.py    tags, cover art, waveform peak cache, display cleanup
  history.py     past sessions from the journal, "put back"
  duplicates.py  "already sorted" index
  playlists.py   M3U8 export
  keys.py        Camelot key parsing
  updates.py     GitHub release check
  api.py         backend methods exposed to the UI
  server.py      local HTTP server (UI, audio with Range requests, JSON API)
  app.py         pywebview window, folder dialog, drag and drop, full screen
  web/           index.html, app.css, app.js, panels.js
main.py          entry point
tests/           pytest suite
```

`bakkis_music_sorter.py` is the original v1 (Tkinter) and is kept for reference until v2 is released.

The app icon is drawn by `tools/make_icon.py` (writes `assets/icon/icon-1024.png` and `AppIcon.icns`).

Building the `.app` is described in [PACKAGING.md](PACKAGING.md).
