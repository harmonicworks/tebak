#!/usr/bin/env bash
# 테박 launcher — sets up venv if needed, then runs
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV/bin/python" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q textual mido python-rtmidi
    echo "Done."
fi

exec "$VENV/bin/python" tebak.py
