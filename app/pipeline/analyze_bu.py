from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional
import inspect
import subprocess
import tempfile

import mido

from app import config
from app.midi.model import NoteEvent


# -----------------------------
# Public API
# -----------------------------

def analyze_audio(input_path: Path) -> Tuple[List[NoteEvent], int, List[str]]:
    """
    Analyze/transcribe audio to a list of NoteEvent using Basic Pitch.
    Returns: (notes, sample_rate, warnings)

    Notes:
    - We decode MP3/etc -> WAV via FFmpeg first (reliable).
    - Basic Pitch produces a MIDI file; we read it back into NoteEvents.
    - If anything fails, we return dummy notes and log the real error in warnings.
    """
    warnings: List[str] = []
    sr_out = 44100  # this return value isn't critical for MIDI, but keep stable

    try:
        # 1) Decode to WAV (Basic Pitch is safer with WAV than MP3)
        wav_path = _decode_to_wav(input_path, warnings=warnings)

        # 2) Find the bundled Basic Pitch model directory
        model_dir = _find_basic_pitch_tf_model_dir()

        # 3) Run Basic Pitch to produce MIDI
        with tempfile.TemporaryDirectory(prefix="wavenotes_basic_pitch_") as tmp:
            tmpdir = Path(tmp)
            wav_path = _decode_to_wav(input_path, out_dir=tmpdir, warnings=warnings)
            model_dir = _find_basic_pitch_tf_model_dir()
            midi_path = _run_basic_pitch(wav_path, tmpdir, model_dir=model_dir)
        # 4) Parse MIDI -> NoteEvent list
            notes = _midi_to_notes(midi_path)
            return notes, sr_out, warnings
        warnings.append("Used basic_pitch successfully.")
        return notes, sr_out, warnings
    except Exception as e:
        warnings.append(f"basic_pitch failed: {type(e).__name__}: {e}")
        warnings.append("Falling back to dummy notes.")
        return _dummy_notes(), sr_out, warnings


# -----------------------------
# Basic Pitch integration
# -----------------------------

def _find_basic_pitch_tf_model_dir() -> Path:
    """
    Locate the TensorFlow SavedModel directory bundled with the basic_pitch package.
    We search the installed package folder for a 'saved_model.pb' file.
    """
    import basic_pitch  # type: ignore

    pkg_dir = Path(basic_pitch.__file__).resolve().parent

    for p in pkg_dir.rglob("saved_model.pb"):
        return p.parent

    raise FileNotFoundError(
        "Could not find Basic Pitch TensorFlow model (saved_model.pb) inside the installed basic_pitch package."
    )


def _run_basic_pitch(input_wav: Path, out_dir: Path, model_dir: Path) -> Path:
    """
    Runs basic_pitch.inference.predict_and_save and returns the produced .mid path.
    Handles signature differences across basic-pitch versions.
    """
    from basic_pitch.inference import predict_and_save  # type: ignore

    out_dir.mkdir(parents=True, exist_ok=True)

    sig = inspect.signature(predict_and_save)

    # Required args in newer versions:
    kwargs = {
        "sonify_midi": False,
        "model_or_model_path": str(model_dir),
        "save_midi": True,
        "save_notes": False,
    }

    # Some versions have additional optional args; set them only if supported.
    if "save_model_outputs" in sig.parameters:
        kwargs["save_model_outputs"] = False

    predict_and_save([str(input_wav)], str(out_dir), **kwargs)

    mids = sorted(out_dir.glob("*.mid"))
    if not mids:
        raise RuntimeError("basic_pitch ran but did not produce a MIDI file.")

    return mids[0]


# -----------------------------
# Audio decode helper (FFmpeg)
# -----------------------------

def _ffmpeg_exe() -> str:
    bundled = config.FFMPEG_DIR / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"


def _decode_to_wav(input_path: Path, out_dir: Path, warnings: Optional[List[str]] = None) -> Path:
    """
    Decode any supported audio file (mp3/wav/flac/m4a/...) to a temporary WAV using FFmpeg.
    Returns path to a WAV file.
    """
    if warnings is None:
        warnings = []

    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    ffmpeg = _ffmpeg_exe()

    tmpdir = Path(tempfile.mkdtemp(prefix="wavenotes_decode_"))
    wav_path = tmpdir / f"{input_path.stem}.wav"

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(input_path),
        "-ac", "1",          # mono
        "-ar", "16000",      # resample to a common ML-friendly rate
        "-f", "wav",
        str(wav_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0 or not wav_path.exists():
        # include stderr so we see the real decode failure
        raise RuntimeError(f"FFmpeg decode failed:\n{result.stderr}")

    return wav_path


# -----------------------------
# MIDI parsing -> NoteEvent
# -----------------------------

def _midi_to_notes(midi_path: Path) -> List[NoteEvent]:
    """
    Reads a MIDI file and extracts NoteEvent objects.
    Assumes note_on with velocity > 0 starts a note; note_off or note_on vel=0 ends it.
    """
    mid = mido.MidiFile(str(midi_path))

    # Use MIDI tempo map to convert ticks -> seconds properly
    # mido provides tick2second but needs tempo; if multiple tempo changes exist,
    # we'd need a full tempo map integration. For Basic Pitch output, this is usually fine.
    tempo = 500000  # default 120 BPM
    ticks_per_beat = mid.ticks_per_beat

    current_time_sec = 0.0
    active = {}  # (channel, pitch) -> (start_sec, velocity)

    notes: List[NoteEvent] = []

    for track in mid.tracks:
        current_time_sec = 0.0
        active.clear()

        for msg in track:
            # Advance time
            if msg.time:
                current_time_sec += mido.tick2second(msg.time, ticks_per_beat, tempo)

            if msg.type == "set_tempo":
                tempo = msg.tempo
                continue

            if msg.type == "note_on" and msg.velocity > 0:
                key = (getattr(msg, "channel", 0), msg.note)
                active[key] = (current_time_sec, msg.velocity)

            elif msg.type in ("note_off", "note_on"):
                # note_on with velocity 0 is treated as note_off
                if msg.type == "note_on" and msg.velocity != 0:
                    continue

                key = (getattr(msg, "channel", 0), msg.note)
                if key in active:
                    start_sec, vel = active.pop(key)
                    end_sec = max(start_sec + 0.001, current_time_sec)
                    notes.append(
                        NoteEvent(
                            start_sec=float(start_sec),
                            end_sec=float(end_sec),
                            midi_pitch=int(msg.note),
                            velocity=int(vel),
                            channel=int(key[0]),
                        )
                    )

        # If you want multi-track merge, you'd collect across tracks.
        # Basic Pitch usually writes notes in a single relevant track anyway.
        if notes:
            break

    # Sort notes by time
    notes.sort(key=lambda n: (n.start_sec, n.midi_pitch))
    return notes


# -----------------------------
# Dummy fallback
# -----------------------------

def _dummy_notes() -> List[NoteEvent]:
    t = 0.0
    dur = 0.35
    pitches = [60, 64, 67, 72, 67, 64, 60]  # C major-ish
    out: List[NoteEvent] = []
    for p in pitches:
        out.append(NoteEvent(t, t + dur, p, velocity=96, channel=0))
        t += 0.40
    return out
