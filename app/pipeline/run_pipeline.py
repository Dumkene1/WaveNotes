from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional

from app.audio.io import load_audio_mono
from app.midi.writer import write_midi
from app.state import Settings, TranscriptionResult
from app.transcription.poly_model import PolyphonicStubTranscriber
from app.transcription.postprocess import postprocess_notes
from app import config


ProgressFn = Optional[Callable[[int, str], None]]


def run_pipeline(input_path: Path, settings: Settings, progress: ProgressFn = None) -> TranscriptionResult:
    warnings = []

    def emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    emit(5, "Loading audio…")
    audio, sr, w = load_audio_mono(input_path, target_sr=16000)
    warnings.extend(w)

    emit(35, "Transcribing (stub)…")
    transcriber = PolyphonicStubTranscriber()
    notes = transcriber.transcribe(audio, sr, settings)

    emit(70, "Post-processing…")
    notes = postprocess_notes(notes, settings)

    emit(85, "Writing MIDI…")
    out_dir = config.DEFAULT_EXPORT_DIR
    out_path = out_dir / (input_path.stem + ".mid")
    write_midi(notes, out_path)

    emit(100, "Done.")
    return TranscriptionResult(input_path=input_path, midi_path=out_path, notes=notes, warnings=warnings)
