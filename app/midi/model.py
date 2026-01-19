from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class NoteEvent:
    start_sec: float
    end_sec: float
    midi_pitch: int
    velocity: int = 96
    channel: int = 0  # 0-15
