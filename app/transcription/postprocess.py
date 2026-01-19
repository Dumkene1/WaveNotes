from __future__ import annotations

from typing import List
from app.state import Settings, NoteEvent


def _grid_seconds(bpm: int, grid_str: str) -> float:
    bpm = max(1, int(bpm))
    quarter = 60.0 / bpm
    try:
        denom = int(str(grid_str).split("/")[1])
    except Exception:
        denom = 16
    denom = max(1, denom)
    return quarter * (4.0 / denom)


def _quantize_time(t: float, grid: float, strength: float) -> float:
    if grid <= 0:
        return t
    target = round(t / grid) * grid
    return t + (target - t) * strength


def _apply_quantize(notes: List[NoteEvent], s: Settings) -> List[NoteEvent]:
    grid = _grid_seconds(s.quantize_bpm, s.quantize_grid)
    strength = max(0.0, min(1.0, s.quantize_strength / 100.0))
    if strength <= 0.0:
        return notes

    min_dur = max(0.001, s.min_note_ms / 1000.0)
    out: List[NoteEvent] = []
    for n in notes:
        st_q = _quantize_time(n.start_sec, grid, strength)
        en_q = _quantize_time(n.end_sec, grid, strength)
        if en_q < st_q:
            st_q, en_q = en_q, st_q
        if en_q - st_q < min_dur:
            en_q = st_q + min_dur
        out.append(NoteEvent(st_q, en_q, int(n.midi_pitch), int(n.velocity)))
    return out


def _merge_gap(notes: List[NoteEvent], gap_sec: float) -> List[NoteEvent]:
    if gap_sec <= 0:
        return sorted(notes, key=lambda n: (n.start_sec, n.midi_pitch))

    notes = sorted(notes, key=lambda n: (n.midi_pitch, n.start_sec, n.end_sec))
    out: List[NoteEvent] = []
    i = 0
    while i < len(notes):
        cur = notes[i]
        st = cur.start_sec
        en = cur.end_sec
        vel = cur.velocity
        p = cur.midi_pitch
        j = i + 1
        while j < len(notes) and notes[j].midi_pitch == p:
            nxt = notes[j]
            if nxt.start_sec <= en + gap_sec:
                en = max(en, nxt.end_sec)
                vel = max(vel, nxt.velocity)
                j += 1
            else:
                break
        out.append(NoteEvent(st, en, p, vel))
        i = j
    return sorted(out, key=lambda n: (n.start_sec, n.midi_pitch))


def _cap_polyphony(notes: List[NoteEvent], max_polyphony: int) -> List[NoteEvent]:
    max_polyphony = int(max_polyphony)
    if max_polyphony <= 0:
        return sorted(notes, key=lambda n: (n.start_sec, n.midi_pitch))

    notes = sorted(notes, key=lambda n: (n.start_sec, -n.velocity))
    active: List[NoteEvent] = []
    kept: List[NoteEvent] = []

    def prune(t: float) -> None:
        nonlocal active
        active = [a for a in active if a.end_sec > t]

    for n in notes:
        prune(n.start_sec)
        kept.append(n)
        active.append(n)
        if len(active) > max_polyphony:
            worst = min(active, key=lambda x: x.velocity)
            try:
                kept.remove(worst)
            except ValueError:
                pass
            active.remove(worst)

    return sorted(kept, key=lambda n: (n.start_sec, n.midi_pitch))


def apply_tweaks(raw_notes: List[NoteEvent], s: Settings) -> List[NoteEvent]:
    min_dur = max(0.0, s.min_note_ms / 1000.0)
    pmin = int(s.pitch_min)
    pmax = int(s.pitch_max)
    min_vel = int(s.min_velocity)

    out: List[NoteEvent] = []
    for n in raw_notes:
        if n.midi_pitch < pmin or n.midi_pitch > pmax:
            continue
        if n.end_sec <= n.start_sec:
            continue
        if (n.end_sec - n.start_sec) < min_dur:
            continue
        if int(n.velocity) < min_vel:
            continue
        out.append(NoteEvent(float(n.start_sec), float(n.end_sec), int(n.midi_pitch), int(n.velocity)))

    out = _merge_gap(out, s.merge_gap_ms / 1000.0)
    out = _cap_polyphony(out, s.max_polyphony)

    if s.quantize:
        out = _apply_quantize(out, s)

    v = max(1, min(127, int(s.velocity)))
    out = [NoteEvent(n.start_sec, n.end_sec, n.midi_pitch, v) for n in out]
    return sorted(out, key=lambda n: (n.start_sec, n.midi_pitch))
