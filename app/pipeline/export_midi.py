from __future__ import annotations
from pathlib import Path

from app.midi.writer import write_midi
from app.state import AnalysisSession


def export_midi(session: AnalysisSession, out_path: Path) -> Path:
    write_midi(session.current_notes, out_path)
    return out_path
