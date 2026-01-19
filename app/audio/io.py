from __future__ import annotations

from pathlib import Path
from typing import Tuple, List, Optional
import subprocess
import os

import numpy as np
import soundfile as sf

from app import config


def _ffmpeg_exe() -> str:
    bundled = config.FFMPEG_DIR / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"  # fallback to PATH


def decode_to_wav(
    input_path: Path,
    out_wav_path: Path,
    target_sr: int = 16000,
) -> Tuple[Path, List[str]]:
    """
    Decode any audio (mp3/wav/etc.) to a WAV file at out_wav_path using FFmpeg.
    """
    warnings: List[str] = []

    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    out_wav_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_exe()

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(input_path),
        "-ac", "1",
        "-ar", str(target_sr),
        "-vn",
        str(out_wav_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "FFmpeg not found. Put ffmpeg.exe in assets/ffmpeg/ (and required bin files) or install FFmpeg on PATH."
        )

    if proc.returncode != 0 or not out_wav_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "FFmpeg failed to decode:\n"
            f"Input: {input_path}\n"
            f"Command: {' '.join(cmd)}\n\n"
            f"{err}"
        )

    return out_wav_path, warnings


def load_wav_mono_float32(wav_path: Path) -> Tuple[np.ndarray, int]:
    """
    Load decoded WAV into mono float32 audio.
    """
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if isinstance(audio, np.ndarray) and audio.ndim > 1:
        audio = audio.mean(axis=1).astype(np.float32)
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    return audio, sr
