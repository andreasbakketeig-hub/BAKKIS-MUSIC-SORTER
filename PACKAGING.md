# Packaging BAKKIS MUSIC SORTER for macOS

## What you get

- **`dist/BAKKIS MUSIC SORTER.app`** — double-click GUI app (no Terminal).
- Python and **mutagen** are bundled. **tkinter** uses the Python framework linked into the build.
- **Playback** uses **mpv** (JSON IPC): one subprocess started at launch. **mpv** is bundled **if** it is on your `PATH` when you run the build script (typically `/opt/homebrew/bin/mpv` or `/usr/local/bin/mpv`). Otherwise testers install it with Homebrew.

## Build (on your Mac)

1. Put assets next to `bakkis_music_sorter.py` (optional but recommended):
   - `bakkislogo.png` — footer logo  
   - `genre_labels.json` — copied into the app bundle and seeded on first launch  
   - `bakkis_settings.json` — optional seed for defaults  

2. Use Python 3 with a venv (recommended):

   ```bash
   cd "/path/to/BAKKIS MUSIC SORTER"
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. (Recommended) Install **mpv** so it gets bundled:

   ```bash
   brew install mpv
   ```

4. Run the build script:

   ```bash
   chmod +x build_mac_app.sh
   ./build_mac_app.sh
   ```

5. Output: **`dist/BAKKIS MUSIC SORTER.app`**

## Where settings live (for your friends)

When running the **.app**, user-writable files are **not** inside the bundle (the bundle is effectively read-only after copy). They are stored in:

`~/Library/Application Support/BAKKIS MUSIC SORTER/`

- `genre_labels.json`
- `bakkis_settings.json`

On first launch, if those files are missing, the app copies defaults from inside the bundle (if you packaged them).

## Share with friends

1. **Zip the .app** (avoids Gatekeeper stripping quarantine oddly in some cases):

   ```bash
   cd dist
   zip -r "BAKKIS-MUSIC-SORTER-mac.zip" "BAKKIS MUSIC SORTER.app"
   ```

2. Send **`BAKKIS-MUSIC-SORTER-mac.zip`**.

3. **Unsigned apps**: Your friends should unzip, then **right-click the app → Open** the first time and confirm. After that, double-click is fine.

4. If you did **not** bundle mpv, they should install:

   ```bash
   brew install mpv
   ```

   The app also shows an in-app hint when mpv is missing.

## Audio stops when quitting

Closing the window runs **`_stop_playback()`** (pause + stop current media) and **`_mpv_shutdown()`** (quit the mpv process cleanly).

## Troubleshooting

- **“App is damaged” / won’t open**: Usually Gatekeeper. Use **right-click → Open**, or System Settings → Privacy & Security → allow.
- **Blank window / Tcl/Tk issues**: Rare with python.org or Homebrew Pythons; build using the same Python you tested with `python3 bakkis_music_sorter.py`.
