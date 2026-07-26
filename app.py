"""테박 — terminal MIDI step sequencer."""
from __future__ import annotations

import math
import os
from typing import List

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
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
    """Generate reel frames with rotating 3-spoke hub."""
    cx, cy = w // 2, h // 2
    r_outer = min(cx - 1, cy - 1)
    r_hub = 1
    r_spoke = r_outer - 2
    spoke_chars = {0: "─", 1: "╱", 2: "│", 3: "╲"}  # 4 angle buckets

    def angle_char(rad: float) -> str:
        a = rad % math.pi
        idx = round(a / (math.pi / 4)) % 4
        return spoke_chars[idx]

    frames: List[List[str]] = []
    for f in range(num_frames):
        base_angle = f * (2 * math.pi / num_frames)
        grid = [[" "] * w for _ in range(h)]

        # Outer ring (dots)
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            x = cx + round((r_outer - 0.5) * math.cos(rad) * 0.55)
            y = cy + round((r_outer - 0.5) * math.sin(rad))
            if 0 <= x < w and 0 <= y < h:
                grid[y][x] = "·"

        # 3 spokes
        for s in range(3):
            angle = base_angle + s * (2 * math.pi / 3)
            ch = angle_char(angle)
            for step in range(1, r_spoke + 1):
                x = cx + round(step * math.cos(angle) * 0.55)
                y = cy + round(step * math.sin(angle))
                if 0 <= x < w and 0 <= y < h and (x, y) != (cx, cy):
                    grid[y][x] = ch

        # Hub
        hub_ch = "◐◓◑◒"[f % 4]
        grid[cy][cx] = hub_ch

        # Bounding box corners
        grid[0][0] = " "
        grid[0][-1] = " "
        grid[-1][0] = " "
        grid[-1][-1] = " "

        frames.append(["".join(row) for row in grid])
    return frames


REEL_FRAMES = _build_reel_frames()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class StatusBar(Widget):
    """Top bar: name · tempo · position · status."""

    DEFAULT_CSS = "StatusBar { height: 1; }"

    tempo: reactive[float] = reactive(120.0)
    position: reactive[int] = reactive(0)
    status: reactive[str] = reactive("정지")

    def render(self) -> RenderableType:
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(" 테박 ", style="bold #e8a020 on #0d0d0d")
        t.append("  ┃  ", style="#333333")
        t.append(f" 박자: {self.tempo:.0f} BPM ", style="#e8a020")
        t.append("  ┃  ", style="#333333")
        pos = f"{self.position + 1:02d} / 16"
        t.append(f" 위치: {pos} ", style="#4a9eff")
        t.append("  ┃  ", style="#333333")
        color = "#44dd44" if self.status == "재생중" else ("#ff4444" if "녹음" in self.status else "#888888")
        t.append(f" {self.status} ", style=f"bold {color}")
        return t


class TrackRow(Widget):
    """One track: header + 16 step blocks + note strip."""

    DEFAULT_CSS = "TrackRow { height: 4; }"

    playhead: reactive[int] = reactive(-1)
    cursor: reactive[int] = reactive(0)
    focused: reactive[bool] = reactive(False)
    flash: reactive[int] = reactive(-1)  # step index that just fired

    def __init__(self, track_idx: int, track, **kwargs) -> None:
        super().__init__(**kwargs)
        self.track_idx = track_idx
        self.track = track

    def render(self) -> RenderableType:
        t = Text(no_wrap=True)
        trk = self.track
        ph = self.playhead
        cur = self.cursor
        foc = self.focused

        # ---- header row ----
        sel_marker = "▶" if foc else " "
        header = f"{sel_marker} {trk.name}  CH:{trk.channel}"
        t.append(f"{header:<14}", style="#888888" if not foc else "#cccccc")
        t.append("┃ ", style="#444444")

        # ---- step blocks ----
        for i, step in enumerate(trk.steps):
            on_cursor = foc and (i == cur)
            on_head = i == ph
            just_fired = i == self.flash

            if step.active:
                if just_fired or on_head and step.active:
                    bg = " on #3a2500"
                    fg = "#ffdd55"
                elif on_cursor:
                    bg = " on #2a1800"
                    fg = "#ff8c00"
                else:
                    bg = ""
                    fg = "#c87010"
                t.append("██", style=f"bold {fg}{bg}")
            else:
                if on_cursor:
                    t.append("▒▒", style="#555555 on #1e1e1e")
                elif on_head:
                    t.append("░░", style="#444444")
                else:
                    t.append("░░", style="#252525")
            t.append(" ")

        t.append("\n")

        # ---- note strip ----
        t.append(" " * 14)
        t.append("  ", style="")
        for i, step in enumerate(trk.steps):
            on_cursor = foc and (i == cur)
            if step.active:
                nm = note_name(step.note)
                style = "#00aaff" if not on_cursor else "bold #55ddff"
                t.append(f"{nm:<3}", style=style)
            elif on_cursor:
                t.append("─ ─", style="#333333")
            else:
                t.append("   ")

        t.append("\n")

        # ---- separator ----
        t.append("─" * 14, style="#222222")
        t.append("┸─", style="#333333")
        t.append("─" * (16 * 3), style="#1e1e1e")

        return t


class ReelWidget(Widget):
    """Animated tape reel (right panel)."""

    DEFAULT_CSS = "ReelWidget { width: 22; align: center middle; }"

    frame: reactive[int] = reactive(0)
    spinning: reactive[bool] = reactive(False)

    def render(self) -> RenderableType:
        lines = REEL_FRAMES[self.frame % len(REEL_FRAMES)]
        t = Text()
        reel_color = "#c87010" if self.spinning else "#555555"
        for line in lines:
            t.append(line + "\n", style=reel_color)
        label = " ◉ 재생중 " if self.spinning else "  정 지  "
        style = "bold #44dd44" if self.spinning else "dim #555555"
        t.append(label.center(19), style=style)
        return t


class TransportBar(Widget):
    """Bottom bar: play / stop / eject + position scrubber."""

    DEFAULT_CSS = "TransportBar { height: 3; }"

    position: reactive[int] = reactive(0)
    playing: reactive[bool] = reactive(False)
    tempo: reactive[float] = reactive(120.0)
    out_port: reactive[str] = reactive("")

    def render(self) -> RenderableType:
        t = Text()
        play_s = "bold #44dd44" if self.playing else "#444444"
        stop_s = "#888888" if self.playing else "bold #cccccc"

        t.append("\n")
        t.append("  [ ▶ 재생 ]", style=play_s)
        t.append("   [ ■ 정지 ]", style=stop_s)
        t.append("   [ ⏏  꺼내기 ]", style="#888888")
        t.append("      ", style="")

        # scrubber
        bar_w = 32
        filled = round((self.position / 15) * bar_w)
        t.append("─" * filled, style="#c87010")
        t.append("●", style="bold #ffdd55")
        t.append("─" * (bar_w - filled), style="#333333")

        t.append(f"  {self._elapsed_str()}", style="#888888")

        t.append("\n")
        port_label = self.out_port if self.out_port else "포트 없음"
        t.append(f"  MIDI → {port_label}", style="#444444")
        t.append("   ")
        t.append("SPACE 재생/정지  E 꺼내기  R 녹음  ←→ 커서  ↑↓ 음표  [ ] 템포  S 저장  O 불러오기  Q 종료",
                 style="#333333")

        return t

    def _elapsed_str(self) -> str:
        step = self.position
        if self.tempo <= 0:
            return "0:00"
        seconds_per_step = 60.0 / self.tempo / 4.0
        total = step * seconds_per_step
        m, s = divmod(int(total), 60)
        return f"{m}:{s:02d}"


class FileInput(Widget):
    """Floating file path input overlay."""

    DEFAULT_CSS = """
    FileInput {
        layer: overlay;
        align: center middle;
        width: 60;
        height: 5;
        background: #1a1a1a;
        border: solid #555555;
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
        Binding("space", "toggle_play", "재생/정지", show=False),
        Binding("e", "eject", "꺼내기", show=False),
        Binding("r", "toggle_record", "녹음", show=False),
        Binding("s", "save_song", "저장", show=False),
        Binding("o", "open_song", "불러오기", show=False),
        Binding("q", "quit_app", "종료", show=False),
        Binding("left", "cursor_left", "", show=False),
        Binding("right", "cursor_right", "", show=False),
        Binding("up", "note_up", "", show=False),
        Binding("down", "note_down", "", show=False),
        Binding("shift+up", "note_up_octave", "", show=False),
        Binding("shift+down", "note_down_octave", "", show=False),
        Binding("enter", "toggle_step", "", show=False),
        Binding("tab", "next_track", "", show=False),
        Binding("1", "select_track_0", "", show=False),
        Binding("2", "select_track_1", "", show=False),
        Binding("3", "select_track_2", "", show=False),
        Binding("4", "select_track_3", "", show=False),
        Binding("bracketleft", "tempo_down", "", show=False),
        Binding("bracketright", "tempo_up", "", show=False),
        Binding("comma", "nudge_back", "", show=False),
        Binding("period", "nudge_forward", "", show=False),
        Binding("delete", "clear_step", "", show=False),
        Binding("backspace", "clear_step", "", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.seq = Sequencer()
        self.midi = MidiIO()
        self._selected_track = 0
        self._record_mode = False
        self._record_pos = 0
        self._file_mode: str = ""   # "save" or "load"
        self._cursors = [0, 0, 0, 0]
        self._out_port = ""

    # ------------------------------------------------------------ compose

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

    # ------------------------------------------------------------ mount

    def on_mount(self) -> None:
        self.seq.on_step = lambda s: self.call_from_thread(self._on_seq_step, s)
        self.seq.on_note = lambda ti, n, ch: self.midi.play_note(ch, n)

        self._out_port = self.midi.open_output()
        self.midi.open_input()
        self.midi.on_note_in = lambda n, v: self.call_from_thread(self._on_midi_note, n, v)

        transport = self.query_one("#transport", TransportBar)
        transport.out_port = self._out_port
        transport.tempo = self.seq.tempo

        self.set_interval(1 / 12, self._tick_reel)

    # ------------------------------------------------------------ sequencer callbacks

    def _on_seq_step(self, step: int) -> None:
        for i in range(4):
            row = self.query_one(f"#track-{i}", TrackRow)
            row.playhead = step
            # Flash active steps
            if self.seq.tracks[i].steps[step].active:
                row.flash = step
                self.set_timer(0.06, lambda r=row: setattr(r, "flash", -1))

        sb = self.query_one("#status-bar", StatusBar)
        sb.position = step

        tp = self.query_one("#transport", TransportBar)
        tp.position = step

    def _tick_reel(self) -> None:
        reel = self.query_one("#reel", ReelWidget)
        reel.spinning = self.seq.playing
        if self.seq.playing:
            reel.frame += 1

    # ------------------------------------------------------------ MIDI input

    def _on_midi_note(self, note: int, velocity: int) -> None:
        if not self._record_mode:
            return
        trk = self.seq.tracks[self._selected_track]
        s = trk.steps[self._record_pos]
        s.active = True
        s.note = note

        row = self.query_one(f"#track-{self._selected_track}", TrackRow)
        self._record_pos += 1
        if self._record_pos >= 16:
            self._record_mode = False
            self._record_pos = 0
            self._update_status("정지")
        else:
            self._cursors[self._selected_track] = self._record_pos
            row.cursor = self._record_pos
            self._update_status(f"녹음 {self._record_pos + 1}/16")
        row.refresh()

    # ------------------------------------------------------------ actions

    def action_toggle_play(self) -> None:
        if self._record_mode:
            return
        if self.seq.playing:
            self.seq.stop()
            self._update_status("정지")
            tp = self.query_one("#transport", TransportBar)
            tp.playing = False
        else:
            self.seq.play()
            self._update_status("재생중")
            tp = self.query_one("#transport", TransportBar)
            tp.playing = True

    def action_eject(self) -> None:
        self._record_mode = False
        self.seq.eject()
        tp = self.query_one("#transport", TransportBar)
        tp.playing = False
        tp.position = 0
        for i in range(4):
            self.query_one(f"#track-{i}", TrackRow).playhead = -1
        self._update_status("정지")

    def action_toggle_record(self) -> None:
        if self.seq.playing:
            return
        self._record_mode = not self._record_mode
        if self._record_mode:
            self._record_pos = self._cursors[self._selected_track]
            self._update_status(f"녹음 {self._record_pos + 1}/16")
        else:
            self._update_status("정지")

    def action_cursor_left(self) -> None:
        if self._record_mode:
            # backspace: erase current-1 step
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
            # advance with rest
            pos = min(15, self._record_pos)
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
        cur = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.active = not step.active
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_note_up(self) -> None:
        self._shift_note(1)

    def action_note_down(self) -> None:
        self._shift_note(-1)

    def action_note_up_octave(self) -> None:
        self._shift_note(12)

    def action_note_down_octave(self) -> None:
        self._shift_note(-12)

    def _shift_note(self, delta: int) -> None:
        cur = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.note = max(0, min(127, step.note + delta))
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_nudge_back(self) -> None:
        cur = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.nudge = max(-20, step.nudge - 5)

    def action_nudge_forward(self) -> None:
        cur = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.nudge = min(20, step.nudge + 5)

    def action_clear_step(self) -> None:
        cur = self._cursors[self._selected_track]
        step = self.seq.tracks[self._selected_track].steps[cur]
        step.active = False
        step.nudge = 0
        self.query_one(f"#track-{self._selected_track}", TrackRow).refresh()

    def action_tempo_down(self) -> None:
        self.seq.set_tempo(self.seq.tempo - 1)
        self._refresh_tempo()

    def action_tempo_up(self) -> None:
        self.seq.set_tempo(self.seq.tempo + 1)
        self._refresh_tempo()

    def _refresh_tempo(self) -> None:
        self.query_one("#status-bar", StatusBar).tempo = self.seq.tempo
        self.query_one("#transport", TransportBar).tempo = self.seq.tempo

    def action_next_track(self) -> None:
        self._select_track((self._selected_track + 1) % 4)

    def action_select_track_0(self) -> None:
        self._select_track(0)

    def action_select_track_1(self) -> None:
        self._select_track(1)

    def action_select_track_2(self) -> None:
        self._select_track(2)

    def action_select_track_3(self) -> None:
        self._select_track(3)

    def _select_track(self, idx: int) -> None:
        old = self._selected_track
        self._selected_track = idx
        self.query_one(f"#track-{old}", TrackRow).focused = False
        row = self.query_one(f"#track-{idx}", TrackRow)
        row.focused = True
        row.cursor = self._cursors[idx]

    # ------------------------------------------------------------ save / load

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
            self.query_one("#file-input-widget").remove()
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

    # ------------------------------------------------------------ quit

    def action_quit_app(self) -> None:
        self.seq.stop()
        self.midi.close()
        self.exit()

    # ------------------------------------------------------------ helpers

    def _update_status(self, text: str) -> None:
        self.query_one("#status-bar", StatusBar).status = text
