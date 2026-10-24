#!/usr/bin/env bash
# Build BAKKIS MUSIC SORTER.app (universal: Apple Silicon + Intel), a DMG for people to download (drag the app
# to Applications) and a zip for the app's one-click updater. Both go on the GitHub Release.
#
#   ./build_mac_app.sh                 unsigned build (friends open it once via Privacy & Security)
#
#   CODESIGN_IDENTITY="Developer ID Application: Andreas Bakketeig (TEAMID)" \
#   NOTARY_PROFILE="bakkis-notary" ./build_mac_app.sh
#                                      signed + notarized build (opens without a warning)
#
# NOTARY_PROFILE is a keychain profile made once with:
#   xcrun notarytool store-credentials bakkis-notary --apple-id you@example.com --team-id TEAMID
# See PACKAGING.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$ROOT"
[[ "$(uname -s)" == "Darwin" ]] || { echo "macOS only."; exit 1; }

APP_NAME="BAKKIS MUSIC SORTER"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || { /usr/bin/python3 -m venv .venv; }
"$PY" -m pip install -q -r requirements.txt pyinstaller dmgbuild

VERSION="$("$PY" -c 'import bakkis_sorter; print(bakkis_sorter.__version__)')"
ZIP="dist/BAKKIS-MUSIC-SORTER-${VERSION}.zip"
DMG="dist/BAKKIS-MUSIC-SORTER-${VERSION}.dmg"

echo "Building ${APP_NAME} ${VERSION}…"
rm -rf build "dist/${APP_NAME}" "dist/${APP_NAME}.app" "$ZIP" "$DMG"
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/bakkis_music_sorter.spec

APP="dist/${APP_NAME}.app"
[[ -d "$APP" ]] || { echo "Build failed: ${APP} missing"; exit 1; }

# Sanity checks: both architectures, and nothing linked from Homebrew.
EXE="${APP}/Contents/MacOS/${APP_NAME}"
echo "Architectures: $(lipo -archs "$EXE")"
if find "$APP" -type f \( -name '*.so' -o -name '*.dylib' \) -exec otool -L {} \; 2>/dev/null | grep -q -E '/opt/homebrew|/usr/local/(Cellar|opt)'; then
  echo "ERROR: the app links to Homebrew libraries and would not run on other Macs."; exit 1
fi

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --verify --deep --strict --verbose=1 "$APP"
  if [[ -n "${NOTARY_PROFILE:-}" ]]; then
    echo "Notarizing (this takes a few minutes)…"
    ditto -c -k --keepParent "$APP" "$ZIP"
    xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP"
    rm -f "$ZIP"
  fi
fi

ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

# Installer window: background from tools/make_dmg_background.py, layout in packaging/dmg_settings.py.
"$PY" -m dmgbuild -s packaging/dmg_settings.py -D app="$APP" "$APP_NAME" "$DMG" >/dev/null
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --sign "$CODESIGN_IDENTITY" --timestamp "$DMG"
  if [[ -n "${NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
  fi
fi
echo
echo "Done:"
echo "  ${APP}"
echo "  ${DMG} ($(du -h "$DMG" | cut -f1))   ← for people to download"
echo "  ${ZIP} ($(du -h "$ZIP" | cut -f1))   ← for the in-app updater"
[[ -z "${CODESIGN_IDENTITY:-}" ]] && echo "  (unsigned: first launch needs System Settings → Privacy & Security → Open Anyway)"
exit 0
