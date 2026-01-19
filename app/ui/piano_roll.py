from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QFontMetrics
from PySide6.QtWidgets import QWidget

from app.state import NoteEvent


def midi_to_name(midi: int) -> str:
    names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
    octave = (midi // 12) - 1
    return f"{names[midi % 12]}{octave}"


class PianoRollWidget(QWidget):
    selectionChanged = Signal(object)  # NoteEvent or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._notes: List[NoteEvent] = []
        self._selected_index: Optional[int] = None

        self.left_margin = 56
        self.top_margin = 10
        self.px_per_sec = 160.0
        self.px_per_semitone = 10.0

        self.pitch_min = 21
        self.pitch_max = 108
        self.duration_sec = 10.0

        self._t0 = 0.0
        self._playhead_time: Optional[float] = None  # absolute (same scale as NoteEvent.start_sec)

        self.setMouseTracking(True)
        self.setMinimumSize(900, 500)
        self._recompute_virtual_size()

    def set_notes(self, notes: List[NoteEvent]) -> None:
        self._notes = list(notes) if notes else []
        self._selected_index = None

        if self._notes:
            self.pitch_min = max(0, min(n.midi_pitch for n in self._notes) - 2)
            self.pitch_max = min(127, max(n.midi_pitch for n in self._notes) + 2)
            self._t0 = min(n.start_sec for n in self._notes)
            t1 = max(n.end_sec for n in self._notes)
            self.duration_sec = max(1.0, t1 - self._t0)
        else:
            self.pitch_min, self.pitch_max = 21, 108
            self.duration_sec = 10.0
            self._t0 = 0.0

        self._playhead_time = None
        self._recompute_virtual_size()
        self.update()
        self.selectionChanged.emit(None)

    def set_playhead_time(self, time_sec: Optional[float]) -> None:
        self._playhead_time = time_sec
        self.update()

    def time_origin(self) -> float:
        return float(self._t0)

    def _recompute_virtual_size(self) -> None:
        width = int(self.left_margin + self.duration_sec * self.px_per_sec + 200)
        height = int(self.top_margin + (self.pitch_max - self.pitch_min + 1) * self.px_per_semitone + 40)
        self.setMinimumSize(max(900, width), max(500, height))

    def _pitch_to_y(self, midi_pitch: int) -> float:
        return self.top_margin + (self.pitch_max - int(midi_pitch)) * self.px_per_semitone

    def _time_to_x(self, t: float) -> float:
        return self.left_margin + (t - self._t0) * self.px_per_sec

    def _note_rect(self, n: NoteEvent) -> QRectF:
        x = self._time_to_x(n.start_sec)
        w = max(1.0, (n.end_sec - n.start_sec) * self.px_per_sec)
        y = self._pitch_to_y(n.midi_pitch)
        h = self.px_per_semitone
        return QRectF(x, y, w, h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().base())

        if not self._notes:
            p.setPen(QPen(self.palette().text().color()))
            p.drawText(20, 30, "Load + Analyze an audio file to see notes here.")
            return

        t0 = self._t0
        t1 = t0 + self.duration_sec

        text_pen = QPen(self.palette().text().color())
        grid_pen = QPen(self.palette().mid().color())

        # Pitch grid
        for pitch in range(self.pitch_min, self.pitch_max + 1):
            y = self._pitch_to_y(pitch)
            p.setPen(QPen(self.palette().dark().color()) if pitch % 12 == 0 else grid_pen)
            p.drawLine(self.left_margin, int(y), self.width(), int(y))

        # C-note labels
        p.setPen(text_pen)
        fm = QFontMetrics(p.font())
        for pitch in range(self.pitch_min, self.pitch_max + 1):
            if pitch % 12 == 0:
                y = self._pitch_to_y(pitch)
                p.drawText(6, int(y) + fm.ascent(), midi_to_name(pitch))

        # Time grid: 0.5 sec, thicker each 1 sec
        step = 0.5
        t = (int(t0 / step)) * step
        while t < t1 + 1.0:
            x = self._time_to_x(t)
            if abs(t - round(t)) < 1e-6:
                p.setPen(QPen(self.palette().dark().color()))
            else:
                p.setPen(grid_pen)
            p.drawLine(int(x), self.top_margin, int(x), self.height())
            t += step

        # Notes
        p.setPen(Qt.NoPen)
        note_brush = QBrush(self.palette().highlight())
        sel_brush = QBrush(self.palette().highlight().color().darker(130))

        for i, n in enumerate(self._notes):
            r = self._note_rect(n)
            p.setBrush(sel_brush if i == self._selected_index else note_brush)
            p.drawRoundedRect(r, 2, 2)

        # Left margin border
        p.setPen(QPen(self.palette().text().color()))
        p.drawLine(self.left_margin, 0, self.left_margin, self.height())

        # Playhead
        if self._playhead_time is not None:
            x = self._time_to_x(self._playhead_time)
            ph_pen = QPen(self.palette().text().color())
            ph_pen.setWidth(2)
            p.setPen(ph_pen)
            p.drawLine(int(x), 0, int(x), self.height())

    def mousePressEvent(self, event):
        if not self._notes:
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.x(), event.y())

        hit = None
        for i, n in enumerate(self._notes):
            if self._note_rect(n).contains(pos):
                hit = i
                break

        self._selected_index = hit
        self.update()
        self.selectionChanged.emit(self._notes[hit] if hit is not None else None)
