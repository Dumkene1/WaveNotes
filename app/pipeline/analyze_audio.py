from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional

from app.audio.io import decode_to_wav, load_wav_mono_float32
from app.state import Settings, AnalysisSession
from app.transcription.poly_model import PolyphonicStubTranscriber
from app.transcription.postprocess import apply_tweaks


ProgressFn = Optional[Callable[[int, str], None]]


def analyze_audio(
    input_path: Path,
    settings: Settings,
    cache_dir: Path,
    progress: ProgressFn = None,
) -> AnalysisSession:
    warnings = []

    def emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    emit(5, "Decoding audio…")
    decoded_wav = cache_dir / f"{input_path.stem}_decoded.wav"
    wav_path, w = decode_to_wav(input_path, decoded_wav, target_sr=16000)
    warnings.extend(w)

    emit(25, "Loading decoded audio…")
    audio, sr = load_wav_mono_float32(wav_path)

    emit(55, "Transcribing (stub)…")
    transcriber = PolyphonicStubTranscriber()
    raw_notes = transcriber.transcribe(audio, sr, settings)

    emit(75, "Applying tweaks…")
    current_notes = apply_tweaks(raw_notes, settings)

    emit(100, "Analysis complete.")
    return AnalysisSession(
        input_path=input_path,
        decoded_wav_path=wav_path,
        sample_rate=sr,
        raw_notes=raw_notes,
        current_notes=current_notes,
        warnings=warnings,
    )
