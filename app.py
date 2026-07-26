"""테박 — terminal MIDI step sequencer."""
from __future__ import annotations

import math
import os
from typing import List, Optional

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.message import Message
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from midi_io import MidiIO
from sequencer import Sequencer, note_name

# ---------------------------------------------------------------------------
# Reel animation  (pre-generated frames)
# ---------------------------------------------------------------------------

def _build_reel_frames(num_frames: int = 24, w: int = 19, h: int = 11) -> List[List[str]]:
    cx, cy = w // 2, h // 2
    r_outer = min(cx - 1, cy - 1)
    r_spoke = r_outer - 2
    spoke_chars = {0: "─", 1: "╱", 2: "│", 3: "╲"}

    def angle_char(rad: float) -> str:
        a = rad % math.pi
        return spoke_chars[round(a / (math.pi / 4)) % 4]

    frames: List[List[str]] = []
    for f in range(num_frames):
        base_angle = f * (2 * math.pi / num_frames)
        grid = [[" "] * w for _ in range(h)]
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            x = cx + round((r_outer - 0.5) * math.cos(rad) * 0.55)
            y = cy + round((r_outer - 0.5) * math.sin(rad))
            if 0 <= x < w and 0 <= y < h:
                grid[y][x] = "·"
        for s in range(3):
            angle = base_angle + s * (2 * math.pi / 3)
            ch = angle_char(angle)
            for step in range(1, r_spoke + 1):
                x = cx + round(step * math.cos(angle) * 0.55)
                y = cy + round(step * math.sin(angle))
                if 0 <= x < w and 0 <= y < h and (x, y) != (cx, cy):
                    grid[y][x] = ch
        hub_ch = "◐◓◑◒"[f % 4]
        grid[cy][cx] = hub_ch
        for corner in [(0, 0), (0, -1), (-1, 0), (-1, -1)]:
            grid[corner[0]][corner[1]] = " "
        frames.append(["".join(row) for row in grid])
    return frames


REEL_FRAMES = _build_reel_frames()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class StatusBar(Widget):
    DEFAULT_CSS = "StatusBar { height: 1; }"

    tempo:    reactive[float] = reactive(120.0)
    position: reactive[int]   = reactive(0)
    status:   reactive[str]   = reactive("정지")
    record:   reactive[bool]  = reactive(False)

    def render(self) -> RenderableType:
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" 테박 ", style="bold #e8a020 on #111111")
        t.append("  ┃  ", style="#666666")
        t.append(f" 박자: {self.tempo:.0f} BPM ", style="#e8a020")
        t.append("  ┃  ", style="#666666")
        t.append(f" 위치: {self.position + 1:02d} / 16 ", style="#55aaff")
        t.append("  ┃  ", style="#666666")
        if self.record:
            t.append(" ● REC ", style="bold white on #cc0000")
            t.append("  MIDI 키보드로 입력 — SPACE 쉼표   ← 되돌리기   R 종료 ",
                     style="#ff7777")
        else:
            color = "#44ee44" if self.status == "재생중" else "#bbbbbb"
            t.append(f" {self.status} ", style=f"bold {color}")
        return t


class TrackRow(Widget):
    DEFAULT_CSS = "TrackRow { height: 4; }"

    playhead: reactive[int]  = reactive(-1)
    cursor:   reactive[int]  = reactive(0)
    focused:  reactive[bool] = reactive(False)
    flash:    reactive[int]  = reactive(-1)

    def __init__(self, track_idx: int, track, **kwargs) -> None:
        super().__init__(**kwargs)
        self.track_idx = track_idx
        self.track = track

    def render(self) -> RenderableType:
        t = Text(no_wrap=True)
        trk = self.track
        ph, cur, foc = self.playhead, self.cursor, self.focused

        # header
        sel_marker = "▶" if foc else " "
        header = f"{sel_marker} {trk.name}  CH:{trk.channel}"
        t.append(f"{header:<14}", style="#cccccc" if foc else "#999999")
        t.append("┃ ", style="#666666")

        # step blocks
        for i, step in enumerate(trk.steps):
            on_cursor   = foc and (i == cur)
            on_head     = i == ph
            just_fired  = i == self.flash

            if step.active:
                if just_fired or (on_head and step.active):
                    t.append("██", style="bold #ffee44 on #3a2500")
                elif on_cursor:
                    t.append("██", style="bold #ffaa00 on #2a1800")
                else:
                    t.append("██", style="#d08020")
            else:
                if on_cursor:
                    t.append("▒▒", style="#888888 on #252525")
                elif on_head:
                    t.append("░░", style="#666666")
                else:
                    t.append("░░", style="#444444")
            t.append(" ")

        t.append("\n")

        # note strip
        t.append(" " * 14)
        t.append("  ")
        for i, step in enumerate(trk.steps):
            on_cursor = foc and (i == cur)
            if step.active:
                nm = note_name(step.note)
                t.append(f"{nm:<3}", style="bold #55ccff" if on_cursor else "#3399dd")
            elif on_cursor:
                t.append("─ ─", style="#555555")
            else:
                t.append("   ")

        t.append("\n")

        # separator
        t.append("─" * 14, style="#333333")
        t.append("┸─", style="#444444")
        t.append("─" * (16 * 3), style="#2a2a2a")

        return t


class ReelWidget(Widget):
    DEFAULT_CSS = "ReelWidget { width: 22; align: center middle; }"

    frame:    reactive[int]  = reactive(0)
    spinning: reactive[bool] = reactive(False)

    def render(self) -> RenderableType:
        lines = REEL_FRAMES[self.frame % len(REEL_FRAMES)]
        t = Text()
        reel_color = "#d08020" if self.spinning else "#777777"
        for line in lines:
            t.append(line + "\n", style=reel_color)
        label  = " ◉ 재생중 " if self.spinning else "  정  지  "
        style  = "bold #44ee44" if self.spinning else "#777777"
        t.append(label.center(19), style=style)
        return t


class TransportBar(Widget):
    DEFAULT_CSS = "TransportBar { height: 4; }"

    position: reactive[int]   = reactive(0)
    playing:  reactive[bool]  = reactive(False)
    tempo:    reactive[float] = reactive(120.0)
    in_port:  reactive[str]   = reactive("")
    out_port: reactive[str]   = reactive("")

    def render(self) -> RenderableType:
        t = Text()

        # button row
        t.append("\n")
        if self.playing:
            t.append(" ▶ PLAY ", style="bold black on #44dd44")
            t.append("  ")
            t.append(" ■ STOP ", style="#777777")
        else:
            t.append(" ▶ PLAY ", style="#777777")
            t.append("  ")
            t.append(" ■ STOP ", style="bold black on #dddddd")
        t.append("   ")
        t.append(" ⏏  EJECT ", style="#aaaaaa")
        t.append("     ")
        self._hint(t, "SPACE", "play/stop   ")
        self._hint(t, "E",     "eject   ")
        self._hint(t, "R",     "record   ")
        self._hint(t, "TAB/1-4", "track   ")
        self._hint(t, "M",     "MIDI device")

        # scrubber row
        t.append("\n ")
        bar_w  = 40
        pos    = max(0, min(15, self.position))
        filled = round((pos / 15) * bar_w)
        t.append("▕", style="#555555")
        t.append("─" * filled,        style="#d08020")
        t.append("▐",                 style="bold #ffdd55")
        t.append("─" * (bar_w - filled), style="#383838")
        t.append("▏", style="#555555")
        t.append(f"  {self._elapsed()}   ", style="#aaaaaa")
        self._hint(t, "← →",   "cursor   ")
        self._hint(t, "ENTER",  "toggle step   ")
        self._hint(t, "↑ ↓",   "pitch   ")
        self._hint(t, "[ ]",   "tempo   ")
        self._hint(t, "S",     "save   ")
        self._hint(t, "O",     "load   ")
        self._hint(t, "Q",     "quit")

        # MIDI port row
        t.append("\n ")
        if self.in_port or self.out_port:
            t.append("MIDI  IN: ",           style="#777777")
            t.append(self.in_port  or "없음", style="#55aacc")
            t.append("   OUT: ",             style="#777777")
            t.append(self.out_port or "없음", style="#55aacc")
        else:
            t.append("⚠  MIDI 장치 없음 — connect a device and restart  (M to pick device)",
                     style="#cc6666")
        return t

    @staticmethod
    def _hint(t: Text, key: str, label: str) -> None:
        t.append(key,   style="bold #e8a020")
        t.append(f" {label}", style="#888888")

    def _elapsed(self) -> str:
        sps   = 60.0 / max(1.0, self.tempo) / 4.0
        total = self.position * sps
        m, s  = divmod(int(total), 60)
        return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# MIDI device selector overlay
# ---------------------------------------------------------------------------

class MidiSelected(Message):
    def __init__(self, in_port: str, out_port: str) -> None:
        super().__init__()
        self.in_port  = in_port
        self.out_port = out_port


class MidiSelector(Widget):
    """Overlay for picking MIDI IN / OUT ports."""

    DEFAULT_CSS = """
    MidiSelector {
        layer: overlay;
        align: center middle;
        width: 70;
        height: 20;
        background: #1e1e1e;
        border: solid #e8a020;
        padding: 1 2;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("escape", "close",      show=False),
        Binding("tab",    "switch_col", show=False),
        Binding("up",     "move_up",    show=False),
        Binding("down",   "move_down",  show=False),
        Binding("enter",  "confirm",    show=False),
    ]

    def __init__(self, in_ports: List[str], out_ports: List[str],
                 current_in: str, current_out: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.in_ports   = in_ports  or ["(없음)"]
        self.out_ports  = out_ports or ["(없음)"]
        self.in_cursor  = self.in_ports.index(current_in)   if current_in  in in_ports  else 0
        self.out_cursor = self.out_ports.index(current_out) if current_out in out_ports else 0
        self.active_col = 0  # 0 = IN, 1 = OUT

    def render(self) -> RenderableType:
        t = Text()
        t.append(" MIDI 장치 선택  (MIDI Device Select)\n\n", style="bold #e8a020")

        col_w = 30

        # column headers
        in_h  = "▶ 입력 (IN)"  if self.active_col == 0 else "  입력 (IN)"
        out_h = "▶ 출력 (OUT)" if self.active_col == 1 else "  출력 (OUT)"
        t.append(f" {in_h:<{col_w}}", style="bold #55aaff" if self.active_col == 0 else "#888888")
        t.append(f"  {out_h}\n",      style="bold #55aaff" if self.active_col == 1 else "#888888")
        t.append(" " + "─" * col_w + "  " + "─" * col_w + "\n", style="#444444")

        # port lists side by side
        rows = max(len(self.in_ports), len(self.out_ports))
        for i in range(rows):
            # IN column
            if i < len(self.in_ports):
                selected = (self.active_col == 0 and i == self.in_cursor)
                marker   = "▶ " if selected else "  "
                name     = self.in_ports[i][:col_w - 3]
                style    = "bold #e8a020" if selected else "#aaaaaa"
                t.append(f" {marker}{name:<{col_w - 2}}", style=style)
            else:
                t.append(" " * (col_w + 1))
            # OUT column
            if i < len(self.out_ports):
                selected = (self.active_col == 1 and i == self.out_cursor)
                marker   = "▶ " if selected else "  "
                name     = self.out_ports[i][:col_w - 3]
                style    = "bold #e8a020" if selected else "#aaaaaa"
                t.append(f"  {marker}{name}", style=style)
            t.append("\n")

        t.append("\n")
        t.append(" TAB", style="bold #e8a020")
        t.append(" switch column   ", style="#777777")
        t.append("↑ ↓", style="bold #e8a020")
        t.append(" select   ", style="#777777")
        t.append("ENTER", style="bold #e8a020")
        t.append(" confirm   ", style="#777777")
        t.append("ESC", style="bold #e8a020")
        t.append(" cancel", style="#777777")
        return t

    def action_close(self) -> None:
        self.remove()

    def action_switch_col(self) -> None:
        self.active_col = 1 - self.active_col
        self.refresh()

    def action_move_up(self) -> None:
        if self.active_col == 0:
            self.in_cursor = max(0, self.in_cursor - 1)
        else:
            self.out_cursor = max(0, self.out_cursor - 1)
        self.refresh()

    def action_move_down(self) -> None:
        if self.active_col == 0:
            self.in_cursor = min(len(self.in_ports) - 1, self.in_cursor + 1)
        else:
            self.out_cursor = min(len(self.out_ports) - 1, self.out_cursor + 1)
        self.refresh()

    def action_confirm(self) -> None:
        in_p  = self.in_ports[self.in_cursor]   if self.in_ports  else ""
        out_p = self.out_ports[self.out_cursor]  if self.out_ports else ""
        self.post_message(MidiSelected(
            in_port  = "" if in_p  == "(없음)" else in_p,
            out_port = "" if out_p == "(없음)" else out_p,
        ))
        self.remove()


# ---------------------------------------------------------------------------
# File input overlay
# ---------------------------------------------------------------------------

class FileInput(Widget):
    DEFAULT_CSS = """
    FileInput {
        layer: overlay;
        align: center middle;
        width: 64;
        height: 7;
        background: #1e1e1e;
        border: solid #666666;
        padding: 1 2;
    }
    """

    def __init__(self, prompt: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        yield Static(self.prompt, id="file-prompt")
        yield Input(placeholder="경로 입력…", id="file-path-input")


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class TebakApp(App):
    CSS_PATH = "tebak.tcss"

    BINDINGS = [
        Binding("space",       "toggle_play",      "재생/정지",  show=False),
        Binding("e",           "eject",             "꺼내기",    show=False),
        Binding("r",           "toggle_record",     "녹음",      show=False),
        Binding("m",           "open_midi_picker",  "MIDI",      show=False),
        Binding("s",           "save_song",         "저장",      show=False),
        Binding("o",           "open_song",         "불러오기",  show=False),
        Binding("q",           "quit_app",          "종료",      show=False),
        Binding("left",        "cursor_left",       "",          show=False),
        Binding("right",       "cursor_right",      "",          show=False),
        Binding("up",          "note_up",           "",          show=False),
        Binding("down",        "note_down",         "",          show=False),
        Binding("shift+up",    "note_up_octave",    "",          show=False),
        Binding("shift+down",  "note_down_octave",  "",          show=False),
        Binding("enter",       "toggle_step",       "",          show=False),
        Binding("tab",         "next_track",        "",          show=False),
        Binding("1",           "select_track_0",    "",          show=False),
        Binding("2",           "select_track_1",    "",          show=False),
        Binding("3",           "select_track_2",    "",          show=False),
        Binding("4",           "select_track_3",    "",          show=False),
        Binding("bracketleft", "tempo_down",        "",          show=False),
        Binding("bracketright","tempo_up",           "",          show=False),
        Binding("comma",       "nudge_back",        "",          show=False),
        Binding("period",      "nudge_forward",     "",          show=False),
        Binding("delete",      "clear_step",        "",          show=False),
        Binding("backspace",   "clear_step",        "",          show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.seq  = Sequencer()
        self.midi = MidiIO()
        self._selected_track = 0
        self._record_mode    = False
        self._record_pos     = 0
        self._file_mode: str = ""
        self._cursors        = [0, 0, 0, 0]
        self._in_port        = ""
        self._out_port       = ""

    # ---------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-container"):
            with Vertical(id="tracks-container"):
                for i in range(4):
                    row = TrackRow(i, self.seq.tracks[i], id=f"track-{i}")
                    row.focused = (i == 0)
                    yield row
            yield ReelWidget(id="reel")
        yield TransportBar(id="transport")

    # ---------------------------------------------------------------- mount

    def on_mount(self) -> None:
        self.seq.on_step = lambda s: self.call_from_thread(self._on_seq_step, s)
        self.seq.on_note = lambda ti, n, ch: self.midi.play_note(ch, n)

        self._out_port = self.midi.open_output()
        self._in_port  = self.midi.open_input()
        self.midi.on_note_in = lambda n, v: self.call_from_thread(self._on_midi_note, n, v)

        tp = self.query_one("#transport", TransportBar)
        tp.out_port = self._out_port
        tp.in_port  = self._in_port
        tp.tempo    = self.seq.tempo

        self.set_interval(1 / 12, self._tick_reel)

    # ---------------------------------------------------------------- sequencer callbacks

    def _on_seq_step(self, step: int) -> None:
        for i in range(4):
            row = self.query_one(f"#track-{i}", TrackRow)
            row.playhead = step
            if self.seq.tracks[i].steps[step].active:
                row.flash = step
                self.set_timer(0.06, lambda r=row: setattr(r, "flash", -1))
        self.query_one("#status-bar", StatusBar).position = step
        self.query_one("#transport",  TransportBar).position = step

    def _tick_reel(self) -> None:
        reel = self.query_one("#reel", ReelWidget)
        reel.spinning = self.seq.playing
        if self.seq.playing:
            reel.frame += 1

    # ---------------------------------------------------------------- MIDI input

    def _on_midi_note(self, note: int, velocity: int) -> None:
        if not self._record_mode:
            return
        trk = self.seq.tracks[self._selected_track]
        trk.steps[self._record_pos].active = True
        trk.steps[self._record_pos].note   = note

        self._record_pos += 1
        row = self.query_one(f"#track-{self._selected_track}", TrackRow)
        if self._record_pos >= 16:
            self._record_mode = False
            self._record_pos  = 0
            self.query_one("#status-bar", StatusBar).record = False
            self._update_status("정지")
        else:
            self._cursors[self._selected_track] = self._record_pos
            row.cursor = self._record_pos
            self._update_status(f"녹음 {self._record_pos + 1}/16")
        row.refresh()

    # ---------------------------------------------------------------- MIDI device picker

    def action_open_midi_picker(self) -> None:
        try:
            self.query_one("#midi-selector").remove()
            return
        except Exception:
            pass
        sel = MidiSelector(
            in_ports    = MidiIO.list_inputs(),
            out_ports   = MidiIO.list_outputs(),
            current_in  = self._in_port,
            current_out = self._out_port,
            id="midi-selector",
        )
        self.mount(sel)
        self.set_timer(0.05, sel.focus)

    def on_midi_selected(self, event: MidiSelected) -> None:
        self.midi.close()
        self.midi = MidiIO()
        self._out_port = self.midi.open_output(event.out_port)
        self._in_port  = self.midi.open_input(event.in_port)
        self.midi.on_note_in = lambda n, v: self.call_from_thread(self._on_midi_note, n, v)
        tp = self.query_one("#transport", TransportBar)
        tp.out_port = self._out_port
        tp.in_port  = self._in_port

    # ---------------------------------------------------------------- transport actions

    def action_toggle_play(self) -> None:
        if self._record_mode:
            return
        if self.seq.playing:
            self.seq.stop()
            self._update_status("정지")
            self.query_one("#transport", TransportBar).playing = False
        else:
            self.seq.play()
            self._update_status("재생중")
            self.query_one("#transport", TransportBar).playing = True

    def action_eject(self) -> None:
        self._record_mode = False
        self.query_one("#status-bar", StatusBar).record = False
        self.seq.eject()
        tp = self.query_one("#transport", TransportBar)
        tp.playing  = False
        tp.position = 0
        for i in range(4):
            self.query_one(f"#track-{i}", TrackRow).playhead = -1
        self._update_status("정지")

    def action_toggle_record(self) -> None:
        if self.seq.playing:
            return
        self._record_mode = not self._record_mode
        sb = self.query_one("#status-bar", StatusBar)
        sb.record = self._record_mode
        if self._record_mode:
            self._record_pos = self._cursors[self._selected_track]
            self._update_status(f"녹음 {self._record_pos + 1}/16")
        else:
            self._update_status("정지")

    # ---------------------------------------------------------------- navigation

    def action_cursor_left(self) -> None:
        if self._record_mode:
            pos = max(0, self._record_pos - 1)
            self.seq.tracks[self._selected_track].steps[pos].active = False
            self._record_pos = pos
            self._cursors[self._selected_track] = pos
            row = self.query_one(f"#track-{self._selected_track}", TrackRow)
            row.cursor = pos
            row.refresh()
            self._update_status(f"녹음 {pos + 1}/16")
            return
        cur = max(0, self._cursors[self._selected_track] - 1)
        self._cursors[self._selected_track] = cur
        self.query_one(f"#track-{self._selected_track}", TrackRow).cursor = cur

    def action_cursor_right(self) -> None:
        if self._record_mode:
            pos = self._record_pos
            self.seq.tracks[self._selected_track].steps[pos].active = False
            self._record_pos = min(15, pos + 1)
            self._cursors[self._selected_track] = self._record_pos
            row = self.query_one(f"#track-{self._selected_track}", TrackRow)
            row.cursor = self._record_pos
            row.refresh()
            self._update_status(f"녹음 {self._record_pos + 1}/16")
            return
        cur = min(15, self._cursors[self._selected_track] + 1)
        self._cursors[self._selected_track] = cur
        self.query_one(f"#track-{self._selected_track}", TrackRow).cursor = cur

    def action_toggle_step(self) -> None:
        cur  = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.active = not step.active
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_note_up(self)         -> None: self._shift_note(1)
    def action_note_down(self)       -> None: self._shift_note(-1)
    def action_note_up_octave(self)  -> None: self._shift_note(12)
    def action_note_down_octave(self)-> None: self._shift_note(-12)

    def _shift_note(self, delta: int) -> None:
        cur  = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.note = max(0, min(127, step.note + delta))
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_nudge_back(self) -> None:
        cur  = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.nudge = max(-20, step.nudge - 5)

    def action_nudge_forward(self) -> None:
        cur  = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.nudge = min(20, step.nudge + 5)

    def action_clear_step(self) -> None:
        cur  = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.active = False
        step.nudge  = 0
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_tempo_down(self) -> None:
        self.seq.set_tempo(self.seq.tempo - 1)
        self._refresh_tempo()

    def action_tempo_up(self) -> None:
        self.seq.set_tempo(self.seq.tempo + 1)
        self._refresh_tempo()

    def _refresh_tempo(self) -> None:
        self.query_one("#status-bar", StatusBar).tempo = self.seq.tempo
        self.query_one("#transport",  TransportBar).tempo = self.seq.tempo

    def action_next_track(self)     -> None: self._select_track((self._selected_track + 1) % 4)
    def action_select_track_0(self) -> None: self._select_track(0)
    def action_select_track_1(self) -> None: self._select_track(1)
    def action_select_track_2(self) -> None: self._select_track(2)
    def action_select_track_3(self) -> None: self._select_track(3)

    def _select_track(self, idx: int) -> None:
        old = self._selected_track
        self._selected_track = idx
        self.query_one(f"#track-{old}", TrackRow).focused = False
        row = self.query_one(f"#track-{idx}", TrackRow)
        row.focused = True
        row.cursor  = self._cursors[idx]

    # ---------------------------------------------------------------- save / load

    def action_save_song(self) -> None:
        if self.seq.playing:
            return
        self._file_mode = "save"
        self._show_file_input("저장: 파일 이름 입력 (songs/ 폴더)")

    def action_open_song(self) -> None:
        if self.seq.playing:
            return
        self._file_mode = "load"
        self._show_file_input("불러오기: 파일 경로 입력")

    def _show_file_input(self, prompt: str) -> None:
        try:
            self.query_one("#file-input-widget").remove()
        except Exception:
            pass
        widget = FileInput(prompt, id="file-input-widget")
        self.mount(widget)
        self.set_timer(0.1, lambda: self.query_one("#file-path-input", Input).focus())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        if not path:
            try:
                self.query_one("#file-input-widget").remove()
            except Exception:
                pass
            return
        if self._file_mode == "save":
            if not path.endswith(".json"):
                path += ".json"
            if not os.path.isabs(path):
                path = os.path.join("songs", path)
            os.makedirs(os.path.dirname(path) or "songs", exist_ok=True)
            self.seq.save(path)
            self._update_status(f"저장됨: {path}")
        elif self._file_mode == "load":
            if not os.path.isabs(path):
                path = os.path.join("songs", path)
            if not path.endswith(".json"):
                path += ".json"
            try:
                self.seq.load(path)
                for i in range(4):
                    self.query_one(f"#track-{i}", TrackRow).refresh()
                self._refresh_tempo()
                self._update_status(f"불러옴: {path}")
            except Exception as exc:
                self._update_status(f"오류: {exc}")
        try:
            self.query_one("#file-input-widget").remove()
        except Exception:
            pass
        self.focus()

    # ---------------------------------------------------------------- quit

    def action_quit_app(self) -> None:
        self.seq.stop()
        self.midi.close()
        self.exit()

    def _update_status(self, text: str) -> None:
        self.query_one("#status-bar", StatusBar).status = text
