from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Settings:
    min_note_ms: int = 80
    min_velocity: int = 10
    merge_gap_ms: int = 30
    pitch_min: int = 21
    pitch_max: int = 108
    max_polyphony: int = 10
    velocity: int = 96
    quantize: bool = False
    quantize_bpm: int = 120
    quantize_grid: str = "1/16"
    quantize_strength: int = 60


@dataclass
class NoteEvent:
    start_sec: float
    end_sec: float
    midi_pitch: int
    velocity: int = 96


@dataclass
class AnalysisSession:
    input_path: Path
    decoded_wav_path: Path
    sample_rate: int = 44100
    raw_notes: List[NoteEvent] = field(default_factory=list)
    current_notes: List[NoteEvent] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
