#!/usr/bin/env python3
"""테박 (Tebak) — 4-track tape-machine MIDI step sequencer."""
import os
import sys

# Run from the project directory so relative paths (songs/, tebak.tcss) work
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import TebakApp

if __name__ == "__main__":
    TebakApp().run()
