"""테박 — MIDI input / output via mido + rtmidi."""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

import mido

NOTE_GATE = 0.08  # note-on duration in seconds


class MidiIO:
    def __init__(self) -> None:
        self._out: Optional[mido.ports.BaseOutput] = None
        self._in: Optional[mido.ports.BaseInput] = None
        self._running = False
        self.on_note_in: Optional[Callable[[int, int], None]] = None  # note, velocity

    # ------------------------------------------------------------------ ports

    @staticmethod
    def list_outputs() -> List[str]:
        return mido.get_output_names()

    @staticmethod
    def list_inputs() -> List[str]:
        return mido.get_input_names()

    def open_output(self, name: Optional[str] = None) -> str:
        names = mido.get_output_names()
        if not names:
            return ""
        target = name if (name and name in names) else names[0]
        try:
            self._out = mido.open_output(target)
            return target
        except Exception:
            return ""

    def open_input(self, name: Optional[str] = None) -> str:
        names = mido.get_input_names()
        if not names:
            return ""
        target = name if (name and name in names) else names[0]
        try:
            self._in = mido.open_input(target)
            self._running = True
            threading.Thread(target=self._listen, daemon=True).start()
            return target
        except Exception:
            return ""

    # ------------------------------------------------------------------ send

    def play_note(self, channel: int, note: int, gate: float = NOTE_GATE) -> None:
        if not self._out:
            return
        ch = max(0, min(15, channel - 1))
        n = max(0, min(127, note))
        self._out.send(mido.Message("note_on", channel=ch, note=n, velocity=80))

        def _off():
            time.sleep(gate)
            if self._out:
                self._out.send(mido.Message("note_off", channel=ch, note=n, velocity=0))

        threading.Thread(target=_off, daemon=True).start()

    def all_notes_off(self) -> None:
        if not self._out:
            return
        for ch in range(16):
            self._out.send(mido.Message("control_change", channel=ch, control=123, value=0))

    # ------------------------------------------------------------------ receive

    def _listen(self) -> None:
        while self._running and self._in:
            try:
                for msg in self._in.iter_pending():
                    if msg.type == "note_on" and msg.velocity > 0 and self.on_note_in:
                        self.on_note_in(msg.note, msg.velocity)
            except Exception:
                break
            time.sleep(0.001)

    # ------------------------------------------------------------------ cleanup

    def close(self) -> None:
        self._running = False
        self.all_notes_off()
        if self._out:
            self._out.close()
        if self._in:
            self._in.close()
