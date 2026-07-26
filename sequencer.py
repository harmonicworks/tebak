"""테박 — core sequencer engine."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    n = max(0, min(127, n))
    octave = (n // 12) - 1
    return f"{NOTE_NAMES[n % 12]}{octave}"


@dataclass
class Step:
    active: bool = False
    note: int = 60   # MIDI note number
    nudge: int = 0   # timing offset in ms, -20..+20


@dataclass
class Track:
    channel: int = 1
    name: str = "트랙"
    steps: List[Step] = field(default_factory=lambda: [Step() for _ in range(16)])


class Sequencer:
    NUM_STEPS = 16
    TEMPO_MIN = 20.0
    TEMPO_MAX = 300.0

    def __init__(self) -> None:
        self.tracks: List[Track] = [
            Track(channel=i + 1, name=f"트랙 {i + 1}") for i in range(4)
        ]
        self.tempo: float = 120.0
        self.current_step: int = 0
        self.playing: bool = False

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Callbacks wired up by the UI
        self.on_step: Optional[Callable[[int], None]] = None
        self.on_note: Optional[Callable[[int, int, int], None]] = None  # track_idx, note, ch

    @property
    def step_interval(self) -> float:
        """Duration of one 16th-note step in seconds."""
        return 60.0 / self.tempo / 4.0

    def set_tempo(self, bpm: float) -> None:
        self.tempo = max(self.TEMPO_MIN, min(self.TEMPO_MAX, bpm))

    def play(self) -> None:
        if self.playing:
            return
        self.playing = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._clock_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.playing = False
        self._stop_event.set()

    def eject(self) -> None:
        """Stop and return to step 0."""
        self.stop()
        self.current_step = 0

    def _clock_loop(self) -> None:
        while not self._stop_event.is_set():
            t0 = time.perf_counter()
            step = self.current_step

            for i, track in enumerate(self.tracks):
                s = track.steps[step]
                if s.active and self.on_note:
                    if s.nudge > 0:
                        def _fire(ti=i, tn=s.note, tc=track.channel, d=s.nudge / 1000.0):
                            time.sleep(d)
                            if self.on_note:
                                self.on_note(ti, tn, tc)
                        threading.Thread(target=_fire, daemon=True).start()
                    else:
                        self.on_note(i, s.note, track.channel)

            if self.on_step:
                self.on_step(step)

            self.current_step = (step + 1) % self.NUM_STEPS

            elapsed = time.perf_counter() - t0
            wait = self.step_interval - elapsed
            if wait > 0:
                self._stop_event.wait(timeout=wait)

    # ------------------------------------------------------------------ persist

    def save(self, path: str) -> None:
        data = {
            "tempo": self.tempo,
            "tracks": [
                {
                    "channel": t.channel,
                    "name": t.name,
                    "steps": [
                        {"active": s.active, "note": s.note, "nudge": s.nudge}
                        for s in t.steps
                    ],
                }
                for t in self.tracks
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str) -> None:
        with open(path) as f:
            data = json.load(f)
        self.tempo = float(data.get("tempo", 120.0))
        for i, td in enumerate(data.get("tracks", [])):
            if i >= 4:
                break
            t = self.tracks[i]
            t.channel = td.get("channel", i + 1)
            t.name = td.get("name", f"트랙 {i + 1}")
            for j, sd in enumerate(td.get("steps", [])):
                if j >= 16:
                    break
                t.steps[j].active = sd.get("active", False)
                t.steps[j].note = sd.get("note", 60)
                t.steps[j].nudge = sd.get("nudge", 0)
