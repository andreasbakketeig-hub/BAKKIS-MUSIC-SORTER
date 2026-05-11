#!/usr/bin/env bash
# Build a double-clickable macOS app: dist/BAKKIS MUSIC SORTER.app
# Requires: Python 3.x on the build machine, Xcode CLI tools optional for codesign.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS only."
  exit 1
fi

if [[ -d "${ROOT}/.venv" ]]; then
  # shellcheck source=/dev/null
  source "${ROOT}/.venv/bin/activate"
fi

python3 -m pip install -q -r "${ROOT}/requirements.txt"
python3 -m pip install -q -r "${ROOT}/requirements-packaging.txt"

PYI_ARGS=(
  --noconfirm
  --clean
  --windowed
  --name "BAKKIS MUSIC SORTER"
  --osx-bundle-identifier "com.bakkis.music-sorter"
  --collect-all mutagen
  --collect-all pillow
  --hidden-import PIL._tkinter_finder
)

if [[ -f "${ROOT}/genre_labels.json" ]]; then
  PYI_ARGS+=(--add-data "${ROOT}/genre_labels.json:.")
else
  echo "Note: genre_labels.json not found — first-run defaults come from code; optional seed file skipped."
fi

if [[ -f "${ROOT}/bakkis_settings.json" ]]; then
  PYI_ARGS+=(--add-data "${ROOT}/bakkis_settings.json:.")
fi

if [[ -f "${ROOT}/bakkislogo.png" ]]; then
  PYI_ARGS+=(--add-data "${ROOT}/bakkislogo.png:.")
else
  echo "Note: bakkislogo.png not found — footer shows text only."
fi

if command -v mpv >/dev/null 2>&1; then
  MPV="$(command -v mpv)"
  PYI_ARGS+=(--add-binary "${MPV}:.")
  echo "Bundling mpv: ${MPV}"
else
  echo "Note: mpv not in PATH — testers need: brew install mpv (the app shows this hint if playback fails)."
fi

python3 -m PyInstaller "${PYI_ARGS[@]}" "${ROOT}/bakkis_music_sorter.py"

APP="${ROOT}/dist/BAKKIS MUSIC SORTER.app"
if [[ -d "${APP}" ]]; then
  echo ""
  echo "Done: ${APP}"
  echo "Zip it before sharing:  cd dist && zip -r \"BAKKIS-MUSIC-SORTER-mac.zip\" \"BAKKIS MUSIC SORTER.app\""
else
  echo "Expected app bundle not found at: ${APP}"
  exit 1
fi
