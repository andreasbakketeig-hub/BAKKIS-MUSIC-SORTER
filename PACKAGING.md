# Packaging BAKKIS MUSIC SORTER for macOS

This builds the v2 app (`bakkis_sorter/`). v1 (`legacy/bakkis_music_sorter.py`) is no longer packaged.

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

## Signed and notarized builds (releases)

Releases are signed with a Developer ID and notarized, so they open with a double-click and no warning.

One-time setup on the build Mac (already done on Andreas's Mac Studio, Team ID `Z4R857FVS8`):

1. Apple Developer Program, Individual.
2. A **Developer ID Application** certificate in the login keychain (without Xcode: Keychain Access → Certificate Assistant → Request a Certificate…, upload the CSR at developer.apple.com, double-click the `.cer`). Apple's **Developer ID G2** intermediate (`apple.com/certificateauthority/DeveloperIDG2CA.cer`) must be in the keychain too. Check with `security find-identity -v -p codesigning`.
3. An app-specific password (account.apple.com) stored once in the keychain:
   ```bash
   xcrun notarytool store-credentials bakkis-notary --apple-id <Apple ID> --team-id Z4R857FVS8
   ```

Each release:

```bash
CODESIGN_IDENTITY="Developer ID Application: Andreas Bakketeig (Z4R857FVS8)" \
NOTARY_PROFILE="bakkis-notary" \
./build_mac_app.sh
```

The app is signed with the hardened runtime (`packaging/entitlements.plist` allows what a bundled Python needs), notarized (a few minutes) and stapled; the DMG is signed, notarized and stapled too. Check with `spctl -a -vv "dist/BAKKIS MUSIC SORTER.app"` (should say *Notarized Developer ID*).

The updater trusts this: once the running app is signed, it only installs an update signed by the same Team ID. **Never publish an unsigned build again**, or signed installs will refuse it.

## Unsigned builds (local testing only)

`./build_mac_app.sh` without the two variables gives an ad-hoc signed app. Fine for testing on this Mac; don't release it.

## Releasing

1. Set the version in `bakkis_sorter/__init__.py` (for example `2.0.0`) and commit.
2. Run `./build_mac_app.sh`.
3. Test the app from `dist/`, ideally on a second Mac or a fresh user account without your settings.
4. On GitHub: **Releases → Draft a new release**, tag `v2.0.0`, attach `dist/BAKKIS-MUSIC-SORTER-2.0.0.dmg` and `.zip`, and write what is new.

The app's update check looks at the latest GitHub release, so people get a notice once the release is published (the repository must be public for that).

## Troubleshooting

- **"App is damaged and can't be opened"**: an unsigned build was downloaded. Release only signed + notarized builds; for an old one, `xattr -dr com.apple.quarantine "/Applications/BAKKIS MUSIC SORTER.app"`.
- **Blank window**: run the app's executable from Terminal to see errors:
  `"/Applications/BAKKIS MUSIC SORTER.app/Contents/MacOS/BAKKIS MUSIC SORTER"`. Set `BAKKIS_DEBUG=1` for the Web Inspector.
- **Build fails on a missing module**: add it to `hiddenimports` in `packaging/bakkis_music_sorter.spec`.
