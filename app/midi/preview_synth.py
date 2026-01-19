from __future__ import annotations

from pathlib import Path
from typing import List
import math
import wave
import struct

from app.state import NoteEvent


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((int(midi) - 69) / 12.0))


def render_preview_wav(notes: List[NoteEvent], out_path: Path, sr: int = 44100) -> None:
    sr = int(sr)
    sr = 44100 if sr <= 0 else sr

    if not notes:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\x00\x00" * int(sr * 0.1))
        return

    start0 = min(n.start_sec for n in notes)
    end1 = max(n.end_sec for n in notes)
    length = max(0.1, end1 - start0)
    n_samples = int(length * sr)

    buf = [0.0] * n_samples

    for n in notes:
        st = int(max(0.0, n.start_sec - start0) * sr)
        en = int(max(0.0, n.end_sec - start0) * sr)
        en = min(en, n_samples)
        if en <= st:
            continue
        hz = midi_to_hz(n.midi_pitch)
        amp = max(0.05, min(1.0, n.velocity / 127.0)) * 0.25

        attack = int(0.01 * sr)
        release = int(0.03 * sr)
        for i in range(st, en):
            t = (i - st) / sr
            s = math.sin(2.0 * math.pi * hz * t)
            env = 1.0
            if i - st < attack:
                env = (i - st) / max(1, attack)
            if en - i < release:
                env = min(env, (en - i) / max(1, release))
            buf[i] += s * amp * env

    peak = max(1e-9, max(abs(x) for x in buf))
    norm = 0.95 / peak
    pcm = bytearray()
    for x in buf:
        v = int(max(-32767, min(32767, x * norm * 32767)))
        pcm += struct.pack("<h", v)

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
