#!/bin/bash
# Double-click launcher (Tkinter + afplay): creates venv, installs deps, runs app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

# Prefer Apple Silicon–native interpreter when this Mac is ARM64 (avoids Rosetta mixups).
if [ "$(uname -m)" = "arm64" ] && /usr/bin/arch -arm64 /usr/bin/true 2>/dev/null; then
  SYS_PY=(/usr/bin/arch -arm64 /usr/bin/python3)
else
  SYS_PY=(/usr/bin/python3)
fi

if ! "${SYS_PY[@]}" -c "import sys; assert sys.version_info >= (3, 9)" 2>/dev/null; then
  osascript -e 'display dialog "Need Python 3.9 or newer (/usr/bin/python3). Install Xcode Command Line Tools or Python from python.org, then double-click again." buttons {"OK"} default button "OK"' 2>/dev/null || \
    echo "Need Python 3.9+. Install Xcode CLT or python.org Python."
  exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
REQ_STAMP="$VENV_DIR/.requirements.stamp"

requirements_stamp() {
  # Use shasum if available; otherwise fall back to file size+mtime
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
  else
    stat -f "%z-%m" "$REQ_FILE"
  fi
}

recreate_venv() {
  echo "Setting up Python environment (one-time or after an architecture change)..."
  rm -rf "$VENV_DIR"
  "${SYS_PY[@]}" -m venv "$VENV_DIR"
  "$PYTHON" -m ensurepip --upgrade || true
  "$PIP" install --upgrade pip
  "$PIP" install -r "$REQ_FILE"
  requirements_stamp > "$REQ_STAMP" 2>/dev/null || true
}

if [ ! -x "$PYTHON" ]; then
  recreate_venv
else
  # Recreate venv if it was built with a different CPU arch (e.g. Rosetta vs native).
  VENV_ARCH="$("$PYTHON" -c "import platform; print(platform.machine())" 2>/dev/null || echo "")"
  SYS_ARCH="$("${SYS_PY[@]}" -c "import platform; print(platform.machine())" 2>/dev/null || echo "")"
  if [ -n "$VENV_ARCH" ] && [ -n "$SYS_ARCH" ] && [ "$VENV_ARCH" != "$SYS_ARCH" ]; then
    recreate_venv
  fi
fi

# Recreate venv if requirements.txt changed.
NEW_STAMP="$(requirements_stamp)"
OLD_STAMP=""
if [ -f "$REQ_STAMP" ]; then
  OLD_STAMP="$(cat "$REQ_STAMP" 2>/dev/null || true)"
fi
if [ -n "$OLD_STAMP" ] && [ "$NEW_STAMP" != "$OLD_STAMP" ]; then
  recreate_venv
fi

# Repair pip in existing venv if missing.
if [ ! -x "$PIP" ]; then
  "$PYTHON" -m ensurepip --upgrade || true
fi

# Ensure runtime deps exist; reinstall if imports fail.
if ! "$PYTHON" -c "import mutagen" >/dev/null 2>&1; then
  "$PYTHON" -m ensurepip --upgrade || true
  "$PIP" install --upgrade pip
  "$PIP" install -r "$REQ_FILE"
  requirements_stamp > "$REQ_STAMP" 2>/dev/null || true
fi

# Keep environment clean from older Qt attempts.
unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH

echo ""
echo "Starting BAKKIS MUSIC SORTER…"
echo ""
echo "If Terminal prints a line about \"Secure coding\" and \"restorable state\","
echo "that is a known harmless macOS + Tk message — the app is still running."
echo "Check the Dock and your other desktops for the app window (Python icon)."
echo ""

exec "$PYTHON" "$SCRIPT_DIR/bakkis_music_sorter.py"
