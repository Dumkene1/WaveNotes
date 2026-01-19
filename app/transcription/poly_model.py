from __future__ import annotations
from typing import List
import numpy as np

from app.midi.model import NoteEvent
from app.state import Settings
from app.transcription.base import Transcriber


class PolyphonicStubTranscriber(Transcriber):
    """
    Temporary stub so the whole app works end-to-end immediately.
    Replaces this later with ONNX inference (onnxruntime).
    """
    def transcribe(self, audio: np.ndarray, sr: int, settings: Settings) -> List[NoteEvent]:
        # Make a simple "C major arpeggio" over the first few seconds
        dur = max(2.0, min(8.0, len(audio) / sr))
        pitches = [60, 64, 67, 72]  # C E G C
        step = dur / len(pitches)

        notes: List[NoteEvent] = []
        t = 0.0
        for p in pitches:
            notes.append(NoteEvent(t, t + step * 0.9, p, velocity=settings.velocity))
            t += step
        return notes
