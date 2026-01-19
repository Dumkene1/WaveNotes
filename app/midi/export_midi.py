from __future__ import annotations

from pathlib import Path
from typing import List

from app.state import NoteEvent


def export_midi(notes: List[NoteEvent], out_path: Path, tempo_bpm: int = 120) -> None:
    try:
        import mido
    except Exception as e:
        raise RuntimeError("Missing dependency 'mido'. Install with: pip install mido") from e

    ticks_per_beat = 480
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi.tracks.append(track)

    tempo = mido.bpm2tempo(max(1, int(tempo_bpm)))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))

    events = []
    for n in notes:
        st = float(n.start_sec)
        en = float(n.end_sec)
        pitch = int(n.midi_pitch)
        vel = int(n.velocity)
        events.append((st, 0, ("on", pitch, vel)))
        events.append((en, -1, ("off", pitch, 0)))

    events.sort(key=lambda x: (x[0], x[1]))

    def sec_to_ticks(sec: float) -> int:
        bpm = max(1, int(tempo_bpm))
        beats = sec / (60.0 / bpm)
        return int(round(beats * ticks_per_beat))

    last_tick = 0
    for t_sec, _, payload in events:
        tick = sec_to_ticks(t_sec)
        delta = max(0, tick - last_tick)
        last_tick = tick

        kind, pitch, vel = payload
        if kind == "on":
            track.append(mido.Message("note_on", note=pitch, velocity=vel, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=delta))

    track.append(mido.MetaMessage("end_of_track", time=1))
    midi.save(str(out_path))
