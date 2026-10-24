# Packaging BAKKIS MUSIC SORTER for macOS

This builds the v2 app (`bakkis_sorter/`). v1 (`bakkis_music_sorter.py`) is no longer packaged.

## What you get

- `dist/BAKKIS MUSIC SORTER.app`: a **universal** app (Apple Silicon and Intel), macOS 13 Ventura or later.
- `dist/BAKKIS-MUSIC-SORTER-<version>.dmg`: the installer window people download (drag the app to Applications), about 10 MB.
- `dist/BAKKIS-MUSIC-SORTER-<version>.zip`: used by the app's one-click updater. Upload **both** to the GitHub Release.

Everything is inside the app: Python, pywebview and mutagen. Audio plays in the system WebKit, so there is **no mpv, ffmpeg or Homebrew dependency**. The build script stops with an error if anything links to Homebrew.

User data is never inside the app. It lives in `~/Library/Application Support/BAKKIS MUSIC SORTER/` (settings, presets, history journal, hearts, waveform cache).

## Build

Requirements on the build Mac: Xcode Command Line Tools (`xcode-select --install`). The build uses the Command Line Tools' Python 3.9, which is universal; the script creates `.venv` if it is missing and installs what it needs.

```bash
./build_mac_app.sh
```

The script:

1. installs `requirements.txt` and PyInstaller into `.venv`,
2. builds with `packaging/bakkis_music_sorter.spec` (version from `bakkis_sorter/__init__.py`, icon from `assets/icon/AppIcon.icns`, bundle id `com.bakkis.music-sorter`),
3. checks that both architectures are present and nothing links to Homebrew,
4. zips the app with `ditto` (keeps the bundle intact for Gatekeeper) and builds the DMG with `dmgbuild` (layout in `packaging/dmg_settings.py`, background from `tools/make_dmg_background.py`).

To change the icon: edit and run `tools/make_icon.py`, then rebuild.

## Unsigned builds (default)

Without a Developer ID the app is ad-hoc signed. On first launch macOS asks people to confirm; the steps are in the README ("First launch of an unsigned app"):

- macOS 15 and later: open it once, click **Done**, then **System Settings → Privacy & Security → Open Anyway**.
- macOS 13–14: right-click the app → **Open** → **Open**.

## Signed and notarized builds (needs an Apple Developer account)

One-time setup:

1. Join the Apple Developer Program (Individual is simplest; it shows your own name).
2. In **Xcode → Settings → Accounts → Manage Certificates**, create a **Developer ID Application** certificate.
3. Create an app-specific password at appleid.apple.com, then store notarization credentials in your keychain:
   ```bash
   xcrun notarytool store-credentials bakkis-notary --apple-id you@example.com --team-id YOURTEAMID
   ```

Each release:

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (YOURTEAMID)" \
NOTARY_PROFILE="bakkis-notary" \
./build_mac_app.sh
```

The app is signed with the hardened runtime (`packaging/entitlements.plist` allows what a bundled Python needs), sent to Apple for notarization (a few minutes), stapled, and zipped. It then opens with a double-click on any Mac, no warning.

## Releasing

1. Set the version in `bakkis_sorter/__init__.py` (for example `2.0.0`) and commit.
2. Run `./build_mac_app.sh`.
3. Test the app from `dist/`, ideally on a second Mac or a fresh user account without your settings.
4. On GitHub: **Releases → Draft a new release**, tag `v2.0.0`, attach `dist/BAKKIS-MUSIC-SORTER-2.0.0.dmg` and `.zip`, and write what is new.

The app's update check looks at the latest GitHub release, so people get a notice once the release is published (the repository must be public for that).

## Troubleshooting

- **"App is damaged and can't be opened"**: the download was quarantined. `xattr -dr com.apple.quarantine "/Applications/BAKKIS MUSIC SORTER.app"`, or sign and notarize the build.
- **Blank window**: run the app's executable from Terminal to see errors:
  `"/Applications/BAKKIS MUSIC SORTER.app/Contents/MacOS/BAKKIS MUSIC SORTER"`. Set `BAKKIS_DEBUG=1` for the Web Inspector.
- **Build fails on a missing module**: add it to `hiddenimports` in `packaging/bakkis_music_sorter.spec`.
