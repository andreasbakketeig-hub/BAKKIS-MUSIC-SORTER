#!/bin/bash
# Double-click to start BAKKIS MUSIC SORTER v2 from the source folder.
# Quits a copy that is already running first, so you always get the newest code.
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd -P)"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up for the first time (this takes a minute)…"
  /usr/bin/python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi

pkill -f "Python main.py" 2>/dev/null && sleep 1 || true
exec .venv/bin/python main.py
