from __future__ import annotations
from pathlib import Path
from typing import Iterable

import pretty_midi

from app.midi.model import NoteEvent


def write_midi(note_events: Iterable[NoteEvent], out_path: Path, program: int = 0) -> None:
    """
    Writes a single-track MIDI file.
    program: General MIDI program number (0 = Acoustic Grand Piano)
    """
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program)

    for n in note_events:
        start = max(0.0, float(n.start_sec))
        end = max(start + 0.001, float(n.end_sec))
        pitch = int(max(0, min(127, n.midi_pitch)))
        vel = int(max(1, min(127, n.velocity)))

        inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=start, end=end))

    pm.instruments.append(inst)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(out_path))
