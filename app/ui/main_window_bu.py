from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Optional, List
from PySide6.QtGui import QIcon
import os
import sys
import tempfile
import shutil


from PySide6.QtCore import Qt, QUrl, QObject, Signal, Slot, QThread
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox,
    QGroupBox, QFormLayout, QCheckBox, QSpinBox, QSlider, QComboBox,
    QSplitter, QScrollArea, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QProgressBar
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from app.state import Settings, AnalysisSession, NoteEvent
from app.ui.piano_roll import PianoRollWidget
from app.ui.audio_separation_window import AudioSeparationWindow
from app.transcription.postprocess import apply_tweaks
from app.pipeline.analyze import analyze_audio
from app.midi.preview_synth import render_preview_wav
from app.midi.export_midi import export_midi

def resource_path(relative_path: str) -> str:
    """
    Return an absolute path to a resource. Works for dev and for PyInstaller (--onefile/--onedir).
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)
 
class AnalyzeWorker(QObject):
    finished = Signal(object)  # AnalysisSession
    failed = Signal(str)

    def __init__(self, input_path: Path):
        super().__init__()
        self.input_path = input_path

    @Slot()
    def run(self) -> None:
        try:
            notes, sr, warnings = analyze_audio(self.input_path)
            session = AnalysisSession(
                input_path=self.input_path,
                decoded_wav_path=self.input_path,
                sample_rate=sr,
                raw_notes=notes,
                current_notes=notes,
                warnings=warnings,
            )
            self.finished.emit(session)
        except Exception as e:
            self.failed.emit(str(e))


class SeparationWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)

    def __init__(
        self,
        input_path: str,
        out_dir: str,
        overwrite: bool,
        model: str = "htdemucs_6s",
        output_format: str = "wav",
        flat_naming: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.out_dir = out_dir
        self.overwrite = overwrite
        self.model = (model or "htdemucs_6s").strip()
        self.output_format = (output_format or "wav").lower()
        self.flat_naming = bool(flat_naming)

    @Slot()
    def run(self) -> None:
        """Run Demucs separation and flatten outputs into the chosen output folder."""
        try:
            in_path = Path(self.input_path)
            out_root = Path(self.out_dir)
            out_root.mkdir(parents=True, exist_ok=True)

            # Prefer the 'demucs' executable; fall back to python -m demucs
            # Demucs will create: <out_root>/<model>/<track_name>/*.wav (or .mp3 when --mp3)
            cmd = [sys.executable, "-m", "demucs", "-n", self.model, "--out", str(out_root)]
            if self.output_format == "mp3":
                cmd += ["--mp3", "--mp3-bitrate", "320"]
            cmd.append(str(in_path))

            self.progress.emit(f"Separating audio (Demucs {self.model})…")
            proc = subprocess.run(cmd, capture_output=True, text=True)

            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip() or f"demucs failed with exit code {proc.returncode}"
                self.finished.emit({"ok": False, "out_dir": str(out_root), "error": err})
                return

            # Find Demucs output folder
            model_dir = out_root / self.model
            if not model_dir.exists():
                # Some Demucs installs might use the model name in a slightly different folder name
                # Fallback: pick the newest directory inside out_root
                candidates = [p for p in out_root.iterdir() if p.is_dir()]
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                model_dir = candidates[0] if candidates else out_root

            # Track directory is usually named after the input filename (stem)
            base = in_path.stem
            track_dir = model_dir / base
            if not track_dir.exists():
                # Fallback: pick the newest track-like directory inside model_dir
                tracks = [p for p in model_dir.iterdir() if p.is_dir()]
                tracks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                track_dir = tracks[0] if tracks else model_dir

            ext = ".mp3" if self.output_format == "mp3" else ".wav"

            stem_map = {
                "vocals": "voice",
                "drums": "drums",
                "bass": "bass",
                "other": "other",
                "piano": "piano",
                "guitar": "guitar",
            }

            moved = []
            for src in track_dir.glob(f"*{ext}"):
                stem_name = src.stem.lower()
                if stem_name not in stem_map:
                    continue
                out_name = f"{base}_{stem_map[stem_name]}{ext}"
                dst = out_root / out_name

                if dst.exists():
                    if self.overwrite:
                        try:
                            dst.unlink()
                        except Exception:
                            pass
                    else:
                        continue

                try:
                    src.replace(dst)
                except Exception:
                    import shutil
                    shutil.copy2(src, dst)
                    try:
                        src.unlink()
                    except Exception:
                        pass

                moved.append(dst)

            # If flat naming is disabled, keep Demucs folder structure (no move).
            # Our UI defaults to flat naming True; if False, still report success.
            if not self.flat_naming:
                self.finished.emit({"ok": True, "out_dir": str(out_root), "error": None})
                return

            # Cleanup empty dirs left behind (best-effort)
            try:
                # Remove track_dir, then model_dir if empty
                for p in [track_dir, model_dir]:
                    if p.exists() and p.is_dir():
                        try:
                            next(p.iterdir())
                        except StopIteration:
                            p.rmdir()
            except Exception:
                pass

            if moved:
                self.finished.emit({"ok": True, "out_dir": str(out_root), "error": None})
            else:
                self.finished.emit({
                    "ok": False,
                    "out_dir": str(out_root),
                    "error": f"Demucs finished but no stem files were found in: {track_dir}"
                })

        except FileNotFoundError:
            self.finished.emit({"ok": False, "out_dir": self.out_dir, "error": "Demucs is not available in this build (missing 'demucs' module). Rebuild with demucs installed/bundled."})
        except Exception as e:
            self.finished.emit({"ok": False, "out_dir": self.out_dir, "error": str(e)})

class PreviewRenderWorker(QObject):
    finished = Signal(bool, str)  # ok, message

    def __init__(self, owner: "MainWindow"):
        super().__init__(owner)
        self.owner = owner

    @Slot()
    def run(self) -> None:
        try:
            p = self.owner._ensure_preview_wav(force=True)
            if not p:
                self.finished.emit(False, "No notes to render.")
                return
            self.finished.emit(True, "MIDI preview ready.")
        except Exception as e:
            self.finished.emit(False, f"Preview render failed: {e}")

class TranscribeStemsWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)  # {"ok": bool, "notes_by_stem": dict[str, list[NoteEvent]], "error": str|None}

    def __init__(self, stem_paths: list[str], parent=None):
        super().__init__(parent)
        self.stem_paths = stem_paths

    @Slot()
    def run(self) -> None:
        try:
            notes_by_stem: dict[str, list[NoteEvent]] = {}
            for i, sp in enumerate(self.stem_paths, start=1):
                name = Path(sp).stem
                self.progress.emit(f"Transcribing stem {i}/{len(self.stem_paths)}: {name}")
                notes, sr, warnings = analyze_audio(Path(sp))
                notes_by_stem[name] = notes
            self.finished.emit({"ok": True, "notes_by_stem": notes_by_stem, "error": None})
        except Exception as e:
            self.finished.emit({"ok": False, "notes_by_stem": {}, "error": str(e)})

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WaveNotes")
        self.setMinimumSize(1280, 780)
        self.resize(1400, 820)
        self.setWindowIcon(QIcon(resource_path("assets/icon/WaveNotes.ico")))
        self._session_cache_dir = Path(tempfile.mkdtemp(prefix="wavenotes_"))
        self.session: Optional[AnalysisSession] = None
        self.current_notes: List[NoteEvent] = []
        self._preview_wav_path: Optional[Path] = None
        self._preview_thread: Optional[QThread] = None
        self._preview_worker: Optional[PreviewRenderWorker] = None
        self._notes_t0: float = 0.0  # min start time of current notes for playhead mapping
        self._sep_win: Optional[AudioSeparationWindow] = None

        self.audio_path: str = ""

        # Smooth scroll state
        self._last_scroll_target: Optional[int] = None

        # Players
        self.audio_out = QAudioOutput()
        self.audio_player = QMediaPlayer()
        self.audio_player.setAudioOutput(self.audio_out)

        self.midi_out = QAudioOutput()
        self.midi_player = QMediaPlayer()
        self.midi_player.setAudioOutput(self.midi_out)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        # ================= LEFT =================
        left = QWidget()
        left_layout = QVBoxLayout(left)

        # --- Audio / file selection
        file_box = QGroupBox("Audio")
        file_form = QFormLayout(file_box)
        self.lbl_file = QLabel("No file selected")
        file_form.addRow("Selected", self.lbl_file)

        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("Open")
        btn_row.addWidget(self.btn_open)
        file_form.addRow(btn_row)

        left_layout.addWidget(file_box)

        # --- Playback (AUDIO ONLY on the left)
        pb_box = QGroupBox("Playback (Audio)")
        pb_form = QFormLayout(pb_box)

        arow = QHBoxLayout()
        self.btn_audio_play = QPushButton("Play Audio")
        self.btn_audio_pause = QPushButton("Pause")
        self.btn_audio_stop = QPushButton("Stop")
        self.btn_audio_pause.setEnabled(False)
        self.btn_audio_stop.setEnabled(False)
        arow.addWidget(self.btn_audio_play)
        arow.addWidget(self.btn_audio_pause)
        arow.addWidget(self.btn_audio_stop)
        pb_form.addRow("Audio", arow)

        self.slider_audio = QSlider(Qt.Horizontal)
        self.slider_audio.setRange(0, 1000)
        self.slider_audio.setToolTip("Scrub audio position.")
        pb_form.addRow("Audio seek", self.slider_audio)

        left_layout.addWidget(pb_box)

        # --- Tweaks
        tweaks = QGroupBox("Tweaks")
        t = QFormLayout(tweaks)

        self.spin_min_note = QSpinBox()
        self.spin_min_note.setRange(1, 2000)
        self.spin_min_note.setValue(80)
        self.spin_min_note.setSuffix(" ms")
        self.spin_min_note.setToolTip("Remove notes shorter than this.\nHigher = less noise.")

        self.spin_min_vel = QSpinBox()
        self.spin_min_vel.setRange(0, 127)
        self.spin_min_vel.setValue(10)
        self.spin_min_vel.setToolTip("Remove quiet notes that are likely noise.")

        self.spin_merge_gap = QSpinBox()
        self.spin_merge_gap.setRange(0, 500)
        self.spin_merge_gap.setValue(30)
        self.spin_merge_gap.setSuffix(" ms")
        self.spin_merge_gap.setToolTip("Merge same-pitch notes separated by tiny gaps.")

        pr = QHBoxLayout()
        self.spin_pitch_min = QSpinBox()
        self.spin_pitch_min.setRange(0, 127)
        self.spin_pitch_min.setValue(21)
        self.spin_pitch_min.setToolTip("Minimum pitch to keep.")
        self.spin_pitch_max = QSpinBox()
        self.spin_pitch_max.setRange(0, 127)
        self.spin_pitch_max.setValue(108)
        self.spin_pitch_max.setToolTip("Maximum pitch to keep.")
        pr.addWidget(self.spin_pitch_min)
        pr.addWidget(self.spin_pitch_max)
        self.btn_reset_pitch_range = QPushButton("↺")
        self.btn_reset_pitch_range.setFixedWidth(26)
        self.btn_reset_pitch_range.setToolTip("Reset to default (21–108)")
        self.btn_reset_pitch_range.clicked.connect(lambda: (self.spin_pitch_min.setValue(21), self.spin_pitch_max.setValue(108)))
        pr.addWidget(self.btn_reset_pitch_range)

        self.spin_poly = QSpinBox()
        self.spin_poly.setRange(1, 32)
        self.spin_poly.setValue(10)
        self.spin_poly.setToolTip("Max overlapping notes allowed at once.")

        self.spin_vel = QSpinBox()
        self.spin_vel.setRange(1, 127)
        self.spin_vel.setValue(96)
        self.spin_vel.setToolTip("Velocity for MIDI export and preview audio.")

        self.chk_quantize = QCheckBox("Enable quantize")
        self.chk_quantize.setToolTip("Snap notes toward a rhythmic grid (live).")

        qrow = QHBoxLayout()
        self.spin_bpm = QSpinBox()
        self.spin_bpm.setRange(30, 300)
        self.spin_bpm.setValue(120)
        self.spin_bpm.setSuffix(" BPM")
        self.spin_bpm.setToolTip("Tempo for quantization.")

        self.combo_grid = QComboBox()
        self.combo_grid.addItems(["1/4", "1/8", "1/16"])
        self.combo_grid.setCurrentText("1/16")
        self.combo_grid.setToolTip("Quantization grid.")

        self.slider_q = QSlider(Qt.Horizontal)
        self.slider_q.setRange(0, 100)
        self.slider_q.setValue(60)
        self.slider_q.setToolTip("Quantize strength.\n0 = off, 100 = hard.")

        qrow.addWidget(self.spin_bpm)
        qrow.addWidget(self.combo_grid)
        qrow.addWidget(self.slider_q)
        self.btn_reset_quantize = QPushButton("↺")
        self.btn_reset_quantize.setFixedWidth(26)
        self.btn_reset_quantize.setToolTip("Reset quantize controls (120 BPM, 1/16, 60%)")
        self.btn_reset_quantize.clicked.connect(lambda: (self.spin_bpm.setValue(120), self.combo_grid.setCurrentText("1/16"), self.slider_q.setValue(60)))
        qrow.addWidget(self.btn_reset_quantize)

        row_min_note = QHBoxLayout()
        row_min_note.addWidget(self.spin_min_note)
        self.btn_reset_min_note = QPushButton("↺")
        self.btn_reset_min_note.setFixedWidth(26)
        self.btn_reset_min_note.setToolTip("Reset to default (80 ms)")
        self.btn_reset_min_note.clicked.connect(lambda: self.spin_min_note.setValue(80))
        row_min_note.addWidget(self.btn_reset_min_note)
        t.addRow("Min note length", row_min_note)
        row_min_vel = QHBoxLayout()
        row_min_vel.addWidget(self.spin_min_vel)
        self.btn_reset_min_vel = QPushButton("↺")
        self.btn_reset_min_vel.setFixedWidth(26)
        self.btn_reset_min_vel.setToolTip("Reset to default (10)")
        self.btn_reset_min_vel.clicked.connect(lambda: self.spin_min_vel.setValue(10))
        row_min_vel.addWidget(self.btn_reset_min_vel)
        t.addRow("Min velocity", row_min_vel)
        row_merge_gap = QHBoxLayout()
        row_merge_gap.addWidget(self.spin_merge_gap)
        self.btn_reset_merge_gap = QPushButton("↺")
        self.btn_reset_merge_gap.setFixedWidth(26)
        self.btn_reset_merge_gap.setToolTip("Reset to default (30 ms)")
        self.btn_reset_merge_gap.clicked.connect(lambda: self.spin_merge_gap.setValue(30))
        row_merge_gap.addWidget(self.btn_reset_merge_gap)
        t.addRow("Merge gap", row_merge_gap)
        t.addRow("Pitch range", pr)
        row_poly = QHBoxLayout()
        row_poly.addWidget(self.spin_poly)
        self.btn_reset_poly = QPushButton("↺")
        self.btn_reset_poly.setFixedWidth(26)
        self.btn_reset_poly.setToolTip("Reset to default (10)")
        self.btn_reset_poly.clicked.connect(lambda: self.spin_poly.setValue(10))
        row_poly.addWidget(self.btn_reset_poly)
        t.addRow("Max polyphony", row_poly)
        row_vel = QHBoxLayout()
        row_vel.addWidget(self.spin_vel)
        self.btn_reset_vel = QPushButton("↺")
        self.btn_reset_vel.setFixedWidth(26)
        self.btn_reset_vel.setToolTip("Reset to default (96)")
        self.btn_reset_vel.clicked.connect(lambda: self.spin_vel.setValue(96))
        row_vel.addWidget(self.btn_reset_vel)
        t.addRow("Velocity", row_vel)
        row_qchk = QHBoxLayout()
        row_qchk.addWidget(self.chk_quantize)
        self.btn_reset_quantize_chk = QPushButton("↺")
        self.btn_reset_quantize_chk.setFixedWidth(26)
        self.btn_reset_quantize_chk.setToolTip("Reset to default (disabled)")
        self.btn_reset_quantize_chk.clicked.connect(lambda: self.chk_quantize.setChecked(False))
        row_qchk.addWidget(self.btn_reset_quantize_chk)
        t.addRow(row_qchk)
        t.addRow("Quantize", qrow)

        left_layout.addWidget(tweaks)

        # More Options button
        self.btn_sep = QPushButton("Audio separation…")
        self.btn_sep.setToolTip("Split the audio into stems (vocals, drums, bass, other) before transcription.")
        left_layout.addWidget(self.btn_sep)

        # Transcription action
        self.btn_analyze = QPushButton("Transcribe")
        self.btn_analyze.setEnabled(False)
        left_layout.addWidget(self.btn_analyze)

        # Progress (shown during transcription)
        self.progress_analyze = QProgressBar()
        self.progress_analyze.setFixedHeight(18)
        self.progress_analyze.setTextVisible(False)
        self.progress_analyze.setVisible(False)
        self.progress_analyze.setRange(0, 100)
        self.progress_analyze.setValue(0)
        left_layout.addWidget(self.progress_analyze)

        # Spacer below action area
        left_layout.addStretch(1)

        # ================= RIGHT =================
        right = QWidget()
        right_outer = QVBoxLayout(right)
        right_outer.setContentsMargins(0, 0, 0, 0)

        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)
        right_splitter.setHandleWidth(10)

        # --- Piano roll
        roll_box = QGroupBox("Piano roll")
        roll_layout = QVBoxLayout(roll_box)
        self.piano = PianoRollWidget()
        self.roll_scroll = QScrollArea()
        self.roll_scroll.setWidgetResizable(True)
        self.roll_scroll.setWidget(self.piano)
        roll_layout.addWidget(self.roll_scroll)

        # --- MIDI Playback + Export
        midi_box = QGroupBox("Playback (MIDI) / Export")
        midi_form = QFormLayout(midi_box)

        mrow = QHBoxLayout()
        self.btn_midi_play = QPushButton("Play MIDI")
        self.btn_midi_pause = QPushButton("Pause")
        self.btn_midi_stop = QPushButton("Stop")
        self.btn_midi_play.setEnabled(False)
        self.btn_midi_pause.setEnabled(False)
        self.btn_midi_stop.setEnabled(False)
        mrow.addWidget(self.btn_midi_play)
        mrow.addWidget(self.btn_midi_pause)
        mrow.addWidget(self.btn_midi_stop)

        self.btn_export = QPushButton("Export MIDI")
        self.btn_export.setEnabled(False)
        mrow.addStretch(1)
        mrow.addWidget(self.btn_export)

        midi_form.addRow("MIDI", mrow)

        self.slider_midi = QSlider(Qt.Horizontal)
        self.slider_midi.setRange(0, 1000)
        self.slider_midi.setToolTip("Scrub MIDI preview position.")
        self.slider_midi.setEnabled(False)
        midi_form.addRow("MIDI seek", self.slider_midi)

        # --- Notes preview
        table_box = QGroupBox("Notes preview")
        table_layout = QVBoxLayout(table_box)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Dur", "Pitch", "Note", "Vel"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        table_layout.addWidget(self.table)

        # --- Log
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log)

        # Right stack order
        right_splitter.addWidget(roll_box)
        right_splitter.addWidget(midi_box)
        right_splitter.addWidget(table_box)
        right_splitter.addWidget(log_box)
        right_splitter.setSizes([520, 110, 220, 160])

        right_outer.addWidget(right_splitter)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([440, 860])

        # Signals
        self.btn_open.clicked.connect(self.open_audio)
        self.btn_analyze.clicked.connect(self.start_analyze)
        self.btn_export.clicked.connect(self.export_midi_dialog)
        self.btn_sep.clicked.connect(self.open_separation_window)

        self.btn_audio_play.clicked.connect(self.play_audio)
        self.btn_audio_pause.clicked.connect(self.pause_audio)
        self.btn_audio_stop.clicked.connect(self.stop_audio)

        self.btn_midi_play.clicked.connect(self.play_midi)
        self.btn_midi_pause.clicked.connect(self.pause_midi)
        self.btn_midi_stop.clicked.connect(self.stop_midi)

        # Audio seek
        self.audio_player.positionChanged.connect(self._on_audio_pos)
        self.audio_player.durationChanged.connect(self._on_audio_dur)
        self.slider_audio.sliderMoved.connect(self._on_audio_seek)

        # MIDI seek + playhead
        self.midi_player.positionChanged.connect(self._on_midi_pos)
        self.midi_player.durationChanged.connect(self._on_midi_dur)
        self.midi_player.playbackStateChanged.connect(self._on_midi_state)
        self.slider_midi.sliderMoved.connect(self._on_midi_seek)

        # Live tweak re-apply
        for w in [
            self.spin_min_note, self.spin_min_vel, self.spin_merge_gap,
            self.spin_pitch_min, self.spin_pitch_max, self.spin_poly,
            self.spin_vel, self.chk_quantize, self.spin_bpm,
            self.combo_grid, self.slider_q,
        ]:
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self.reapply_tweaks)
            if hasattr(w, "stateChanged"):
                w.stateChanged.connect(self.reapply_tweaks)
            if hasattr(w, "currentIndexChanged"):
                w.currentIndexChanged.connect(self.reapply_tweaks)

        self._log("WaveNotes ready.")

    def _center_on_screen(self) -> None:
        """Center the window on the current screen and ensure it fits."""
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
            if not screen:
                return
            avail = screen.availableGeometry()
            # Clamp size to available geometry with a small margin
            margin = 40
            max_w = max(200, avail.width() - margin)
            max_h = max(200, avail.height() - margin)
            w = min(self.width(), max_w)
            h = min(self.height(), max_h)
            if (w, h) != (self.width(), self.height()):
                self.resize(w, h)
            fg = self.frameGeometry()
            fg.moveCenter(avail.center())
            self.move(fg.topLeft())
        except Exception:
            return

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        # Center once per show
        if not getattr(self, "_did_center", False):
            self._did_center = True
            self._center_on_screen()

# ---------------- Smooth follow-playhead scrolling ----------------
    def _autoscroll_piano_to_playhead(self, playhead_time_sec: float) -> None:
        """
        Smoothly keep the MIDI playhead visible by scrolling the piano roll horizontally.
        This uses a simple exponential smoothing toward a target scroll position.
        """
        if not hasattr(self, "roll_scroll") or self.roll_scroll is None:
            return
        if not hasattr(self, "piano") or self.piano is None:
            return
        if not hasattr(self.piano, "_time_to_x"):
            return

        try:
            x = int(self.piano._time_to_x(playhead_time_sec))  # type: ignore[attr-defined]
        except Exception:
            return

        viewport_w = self.roll_scroll.viewport().width()
        if viewport_w <= 0:
            return

        hbar = self.roll_scroll.horizontalScrollBar()
        left = hbar.value()
        right = left + viewport_w

        margin = max(40, viewport_w // 3)

        target = None
        if x < left + margin:
            target = max(0, x - margin)
        elif x > right - margin:
            target = max(0, x - viewport_w + margin)

        if target is None:
            self._last_scroll_target = None
            return

        # Smooth scrolling: move partway toward target on each update
        current = hbar.value()
        if self._last_scroll_target is None:
            # First time: start close so it doesn't snap too hard
            self._last_scroll_target = target

        alpha = 0.22  # smaller = smoother, larger = snappier
        new_val = int(round(current + alpha * (target - current)))

        # If close enough, snap to target to avoid jitter
        if abs(target - new_val) <= 2:
            new_val = target

        hbar.setValue(new_val)
        self._last_scroll_target = target

    # ---------------- Audio Separation Window ----------------
    # ---------------- Audio Separation Window ----------------
    def open_separation_window(self) -> None:
        if self._sep_win is None:
            self._sep_win = AudioSeparationWindow(parent=self)
            self._sep_win.separationRequested.connect(self._start_separation)
        # Prefill current audio if available
        try:
            cur = (getattr(self, 'audio_path', '') or '').strip()
            if cur:
                self._sep_win.set_input_path(cur)
        except Exception:
            pass
        self._sep_win.show()
        self._sep_win.raise_()
        self._sep_win.activateWindow()

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(msg)

    # ---------------- File open ----------------
    def open_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open audio", "", "Audio (*.mp3 *.wav *.flac *.ogg)"
        )
        if not path:
            return

        p = Path(path)
        self.audio_path = str(p)
        self.session = AnalysisSession(input_path=p, decoded_wav_path=p, sample_rate=44100)
        self.lbl_file.setText(str(p))

        self.btn_analyze.setEnabled(True)
        self.btn_export.setEnabled(False)

        # Reset MIDI UI
        self.btn_midi_play.setEnabled(False)
        self.btn_midi_pause.setEnabled(False)
        self.btn_midi_stop.setEnabled(False)
        self.slider_midi.setEnabled(False)
        self.piano.set_playhead_time(None)

        # Reset smooth scroll state
        self._last_scroll_target = None

        # Load audio source
        self.audio_player.setSource(QUrl.fromLocalFile(str(p)))
        self.btn_audio_pause.setEnabled(True)
        self.btn_audio_stop.setEnabled(True)

        self._log(f"Selected: {p}")

    # ---------------- Audio playback ----------------
    def play_audio(self) -> None:
        if not self.session:
            return
        self.audio_player.play()

    def pause_audio(self) -> None:
        self.audio_player.pause()

    def stop_audio(self) -> None:
        self.audio_player.stop()

    def _on_audio_dur(self, dur_ms: int) -> None:
        self.slider_audio.setEnabled(dur_ms > 0)

    def _on_audio_pos(self, pos_ms: int) -> None:
        dur = self.audio_player.duration()
        if dur > 0 and not self.slider_audio.isSliderDown():
            self.slider_audio.setValue(int((pos_ms / dur) * 1000))

    def _on_audio_seek(self, value: int) -> None:
        dur = self.audio_player.duration()
        if dur > 0:
            self.audio_player.setPosition(int((value / 1000) * dur))

    # ---------------- Analyze ----------------
    def start_analyze(self) -> None:
        if not self.session:
            return

        self._log("Transcribing…")
        # Show busy progress
        self.progress_analyze.setVisible(True)
        self.progress_analyze.setRange(0, 0)
        self.progress_analyze.setFormat("Transcribing…")
        self.btn_analyze.setEnabled(False)

        self._thread = QThread()
        self._worker = AnalyzeWorker(self.session.input_path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_analyze_done)
        self._worker.failed.connect(self._on_analyze_failed)

        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _on_analyze_done(self, session_obj: object) -> None:
        self.session = session_obj  # type: ignore

        for w in getattr(self.session, "warnings", []):
            w_str = str(w)
            if "used basic_pitch successfully" in w_str.lower() or "used basic pitch successfully" in w_str.lower():
                self._log(w_str)
            else:
                self._log(f"⚠ {w_str}")

        self._log(f"Transcription done. Raw notes: {len(self.session.raw_notes)}")

        self.btn_analyze.setEnabled(True)
        self.btn_export.setEnabled(True)

        # Done
        self.progress_analyze.setRange(0, 100)
        self.progress_analyze.setValue(100)
        self.progress_analyze.setFormat('Done')
        self.progress_analyze.setVisible(False)

        self.btn_midi_play.setEnabled(True)
        self.btn_midi_pause.setEnabled(True)
        self.btn_midi_stop.setEnabled(True)

        self.reapply_tweaks()

        self._kickoff_preview_render()

    def _on_analyze_failed(self, err: str) -> None:
        self._log(f"Transcription failed: {err}")
        QMessageBox.critical(self, "Transcription failed", err)
        self.btn_analyze.setEnabled(True)
        self.progress_analyze.setVisible(False)

    # ---------------- Tweaks ----------------
    def current_settings(self) -> Settings:
        return Settings(
            min_note_ms=int(self.spin_min_note.value()),
            min_velocity=int(self.spin_min_vel.value()),
            merge_gap_ms=int(self.spin_merge_gap.value()),
            pitch_min=int(self.spin_pitch_min.value()),
            pitch_max=int(self.spin_pitch_max.value()),
            max_polyphony=int(self.spin_poly.value()),
            velocity=int(self.spin_vel.value()),
            quantize=bool(self.chk_quantize.isChecked()),
            quantize_bpm=int(self.spin_bpm.value()),
            quantize_grid=str(self.combo_grid.currentText()),
            quantize_strength=int(self.slider_q.value()),
        )

    def reapply_tweaks(self) -> None:
        if not self.session or not getattr(self.session, "raw_notes", None):
            return

        s = self.current_settings()
        self.current_notes = apply_tweaks(self.session.raw_notes, s)
        self.session.current_notes = self.current_notes

        self._notes_t0 = min((n.start_sec for n in self.current_notes), default=0.0)
        self._update_views()

    def _midi_name(self, midi: int) -> str:
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        octave = (int(midi) // 12) - 1
        return f"{names[int(midi) % 12]}{octave}"

    def _update_views(self) -> None:
        self.piano.set_notes(self.current_notes)

        self.table.setRowCount(0)
        for n in self.current_notes[:2000]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            dur = n.end_sec - n.start_sec
            note_name = self._midi_name(n.midi_pitch)
            vals = [
                f"{n.start_sec:.3f}",
                f"{n.end_sec:.3f}",
                f"{dur:.3f}",
                str(n.midi_pitch),
                note_name,
                str(n.velocity),
            ]
            for c, v in enumerate(vals):
                self.table.setItem(row, c, QTableWidgetItem(v))

    def _kickoff_preview_render(self) -> None:
        """Pre-render preview.wav in a background thread so first Play MIDI is instant."""
        if not self.session or not self.current_notes:
            return

        if self._preview_thread is not None:
            try:
                if self._preview_thread.isRunning():
                    return
            except Exception:
                pass
        self._preview_thread = QThread(self)
        self._preview_worker = PreviewRenderWorker(self)
        self._preview_worker.moveToThread(self._preview_thread)

        self._preview_thread.started.connect(self._preview_worker.run)
        self._preview_worker.finished.connect(self._on_preview_render_done)

        self._preview_worker.finished.connect(self._preview_thread.quit)
        self._preview_worker.finished.connect(self._preview_worker.deleteLater)
        self._preview_thread.finished.connect(self._preview_thread.deleteLater)

        self._preview_thread.start()

    def _on_preview_render_done(self, ok: bool, msg: str) -> None:
        if ok:
            self._log(msg)
        else:
            self._log(f"⚠ {msg}")
    # ---------------- MIDI preview (renders wav) ----------------
    def _ensure_preview_wav(self, force: bool = False) -> Optional[Path]:
        if not self.session or not self.current_notes:
            return None

        cache_dir = self._session_cache_dir / "preview"
        cache_dir.mkdir(parents=True, exist_ok=True)

        out = cache_dir / "preview.wav"
        if (not force) and out.exists() and self._preview_wav_path == out:
            return out

        render_preview_wav(self.current_notes, out, sr=int(getattr(self.session, "sample_rate", 44100)))
        self._preview_wav_path = out
        return out

    def play_midi(self) -> None:
        p = self._ensure_preview_wav(force=False)
        if not p:
            self._log("Preparing MIDI preview…")
            self._kickoff_preview_render()
            return

        # Reset smooth scroll target at start
        self._last_scroll_target = None

        self.piano.set_playhead_time(self._notes_t0)
        self.midi_player.setSource(QUrl.fromLocalFile(str(p)))
        self.midi_player.play()

    def pause_midi(self) -> None:
        self.midi_player.pause()

    def stop_midi(self) -> None:
        self.midi_player.stop()
        self.piano.set_playhead_time(None)
        self._last_scroll_target = None

    def _on_midi_dur(self, dur_ms: int) -> None:
        self.slider_midi.setEnabled(dur_ms > 0)

    def _on_midi_pos(self, pos_ms: int) -> None:
        dur = self.midi_player.duration()
        if dur > 0 and not self.slider_midi.isSliderDown():
            self.slider_midi.setValue(int((pos_ms / dur) * 1000))

        playhead_time = self._notes_t0 + (pos_ms / 1000.0)
        self.piano.set_playhead_time(playhead_time)

        # Smooth follow
        self._autoscroll_piano_to_playhead(playhead_time)

    def _on_midi_seek(self, value: int) -> None:
        dur = self.midi_player.duration()
        if dur > 0:
            self.midi_player.setPosition(int((value / 1000) * dur))
            playhead_time = self._notes_t0 + (self.midi_player.position() / 1000.0)
            self._autoscroll_piano_to_playhead(playhead_time)

    def _on_midi_state(self, _state) -> None:
        # When stopped, clear playhead and reset scroll smoothing
        if self.midi_player.playbackState().name == "StoppedState":
            self.piano.set_playhead_time(None)
            self._last_scroll_target = None

    # ---------------- Export MIDI ----------------
    def export_midi_dialog(self) -> None:
        if not self.current_notes:
            QMessageBox.information(self, "Nothing to export", "Analyze audio first.")
            return

        out_path, _ = QFileDialog.getSaveFileName(self, "Export MIDI", "wavenotes.mid", "MIDI (*.mid)")
        if not out_path:
            return

        try:
            export_midi(self.current_notes, Path(out_path), tempo_bpm=int(self.spin_bpm.value()))
            self._log(f"Exported MIDI: {out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))


    def _start_separation(self, payload: dict) -> None:
        """Run Demucs separation in a worker thread (UI stays responsive)."""
        input_path = (payload.get("input_path") or (getattr(self, 'audio_path', '') or '')).strip()
        if not input_path and hasattr(self, "lbl_audio_path"):
            input_path = self.lbl_audio_path.text().strip()
    
        out_dir = (payload.get("output_dir") or "").strip()
        use_subfolder = bool(payload.get("use_subfolder", False))
        overwrite = bool(payload.get("overwrite", False))
        model = (payload.get("model") or "htdemucs_6s")
        output_format = (payload.get("output_format") or "wav")
        flat_naming = bool(payload.get("flat_naming", True))
    
        if not input_path:
            self._log("❌ No audio loaded. Load an audio file first, then separate.")
            return
        if not out_dir:
            self._log("❌ Choose an output folder in Audio Separation first.")
            return
    
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
    
        if use_subfolder:
            base = Path(input_path).stem
            p2 = p / base
            if p2.exists() and not overwrite:
                i = 2
                while (p / f"{base}_{i}").exists():
                    i += 1
                p2 = p / f"{base}_{i}"
            p = p2
            p.mkdir(parents=True, exist_ok=True)
    
        # Update Options window UI (busy)
        if getattr(self, "_sep_win", None):
            try:
                self._sep_win.ed_out.setText(str(p))
                self._sep_win.lbl_sep_status.setText("Separating…")
                self._sep_win.progress_sep.setVisible(True)
                self._sep_win.progress_sep.setRange(0, 0)
            except Exception:
                pass
    
        self._sep_thread = QThread(self)
        self._sep_worker = SeparationWorker(
            input_path=str(input_path),
            out_dir=str(p),
            overwrite=overwrite,
            model=str(model),
            output_format=str(output_format),
            flat_naming=flat_naming,
        )
        self._sep_worker.moveToThread(self._sep_thread)
        self._sep_thread.started.connect(self._sep_worker.run)
        self._sep_worker.progress.connect(self._log)
        self._sep_worker.finished.connect(self._on_separation_finished)
        self._sep_worker.finished.connect(self._sep_thread.quit)
        self._sep_worker.finished.connect(self._sep_worker.deleteLater)
        self._sep_thread.finished.connect(self._sep_thread.deleteLater)
        self._sep_thread.start()
    
    def _on_separation_finished(self, result: dict) -> None:
        ok = bool(result.get("ok"))
        out_dir = str(result.get("out_dir") or "")
        err = result.get("error")
    
        if getattr(self, "_sep_win", None):
            try:
                self._sep_win.progress_sep.setVisible(False)
                self._sep_win.lbl_sep_status.setText(
                    f"Done. Stems saved to: {out_dir}" if ok else f"Separation failed: {err}"
                )
                self._sep_win._refresh_stems_from_folder()
            except Exception:
                pass
    
        if ok:
            self._log(f"✅ Separation complete. Saved stems to: {out_dir}")
        else:
            self._log(f"❌ Separation failed: {err}")
    
    
    def _transcribe_selected_stems(self, payload: dict) -> None:
        """Transcribe selected stem audio files and export MIDI."""
        stem_paths = payload.get("stem_paths") or []
        export_multitrack = bool(payload.get("export_multitrack", True))
    
        if not stem_paths:
            self._log("❌ No stems selected. Check stems in Audio Separation → Generated stems.")
            return
    
        # Where to save results
        out_dir = ""
        if getattr(self, "_sep_win", None):
            try:
                out_dir = self._sep_win.edit_out_dir.text().strip()
            except Exception:
                out_dir = ""
        if not out_dir:
            out_dir = str(Path(stem_paths[0]).parent)
    
        self._log(f"Transcribing {len(stem_paths)} selected stem(s)…")
    
        # Show busy indicator in options window
        if getattr(self, "_sep_win", None):
            try:
                self._sep_win.lbl_sep_status.setText("Transcribing selected stems…")
                self._sep_win.progress_sep.setVisible(True)
                self._sep_win.progress_sep.setRange(0, 0)
            except Exception:
                pass
    
        # Run in background thread so UI doesn't freeze
        self._stem_thread = QThread(self)
        self._stem_worker = TranscribeStemsWorker(stem_paths=stem_paths)
        self._stem_worker.moveToThread(self._stem_thread)
        self._stem_thread.started.connect(self._stem_worker.run)
        self._stem_worker.progress.connect(self._log)
        self._stem_worker.finished.connect(lambda res: self._on_stems_transcribed(res, out_dir, export_multitrack))
        self._stem_worker.finished.connect(self._stem_thread.quit)
        self._stem_worker.finished.connect(self._stem_worker.deleteLater)
        self._stem_thread.finished.connect(self._stem_thread.deleteLater)
        self._stem_thread.start()
    
    def _on_stems_transcribed(self, result: dict, out_dir: str, export_multitrack: bool) -> None:
        ok = bool(result.get("ok"))
        err = result.get("error")
        notes_by_stem = result.get("notes_by_stem") or {}
    
        if getattr(self, "_sep_win", None):
            try:
                self._sep_win.progress_sep.setVisible(False)
            except Exception:
                pass
    
        if not ok:
            self._log(f"❌ Stem transcription failed: {err}")
            if getattr(self, "_sep_win", None):
                try:
                    self._sep_win.lbl_sep_status.setText(f"Stem transcription failed: {err}")
                except Exception:
                    pass
            return

        out_path = Path(out_dir) / "wavenotes_stems.mid"
    
        try:
            if export_multitrack:
                wrote = self._export_multitrack_midi(notes_by_stem, out_path)
                if wrote:
                    self._log(f"✅ Exported multi-track MIDI: {out_path}")
                else:
                    # Fallback: separate MID files (still useful)
                    self._log("⚠ Multi-track export unavailable (mido not installed). Exporting one MID per stem instead.")
                    for stem, notes in notes_by_stem.items():
                        export_midi(notes, Path(out_dir) / f"{stem}.mid", tempo_bpm=int(self.spin_bpm.value()))
                    self._log(f"✅ Exported {len(notes_by_stem)} MIDI file(s) to: {out_dir}")
            else:
                merged = []
                for notes in notes_by_stem.values():
                    merged.extend(notes)
                merged.sort(key=lambda n: (n.start_sec, n.midi_pitch))
                export_midi(merged, out_path, tempo_bpm=int(self.spin_bpm.value()))
                self._log(f"✅ Exported merged MIDI: {out_path}")
        except Exception as e:
            self._log(f"❌ Export failed: {e}")
            return
    # Load one stem into the piano roll for preview (vocals preferred)
        pick = None
        for pref in ["vocals", "vocal", "other", "bass", "drums"]:
            for stem in notes_by_stem.keys():
                if stem.lower().startswith(pref):
                    pick = stem
                    break
            if pick:
                break
        if not pick and notes_by_stem:
            pick = next(iter(notes_by_stem.keys()))
    
        if pick:
            self.current_notes = notes_by_stem[pick]
            self._notes_t0 = min((n.start_sec for n in self.current_notes), default=0.0)
            self._update_views()
            self._log(f"Preview loaded: {pick} ({len(self.current_notes)} notes)")
    
        if getattr(self, "_sep_win", None):
            try:
                self._sep_win.lbl_sep_status.setText(f"Done. MIDI saved in: {out_dir}")
            except Exception:
                pass
    
    def _export_multitrack_midi(self, notes_by_stem: dict, out_path: Path) -> bool:
        """Write a single MIDI with one track per stem. Returns False if mido isn't available."""
        try:
            import mido
            from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo
        except Exception:
            return False
    
        mid = MidiFile(ticks_per_beat=480)
        tempo = bpm2tempo(int(self.spin_bpm.value()))
    
        tempo_track = MidiTrack()
        tempo_track.append(MetaMessage("set_tempo", tempo=tempo, time=0))
        mid.tracks.append(tempo_track)
    
        ticks_per_sec = (mid.ticks_per_beat * 1_000_000) / tempo
    
        def sec_to_ticks(sec: float) -> int:
            return max(0, int(round(sec * ticks_per_sec)))
    
        for stem_name, notes in notes_by_stem.items():
            tr = MidiTrack()
            tr.append(MetaMessage("track_name", name=str(stem_name), time=0))
    
            events = []
            for n in notes:
                on_t = sec_to_ticks(n.start_sec)
                off_t = sec_to_ticks(n.end_sec)
                vel = int(getattr(n, "velocity", 96))
                pitch = int(getattr(n, "midi_pitch", 60))
                events.append((on_t, True, pitch, vel))
                events.append((off_t, False, pitch, 0))
    
            events.sort(key=lambda e: (e[0], not e[1]))  # note_off first at same tick
    
            last = 0
            for t, is_on, pitch, vel in events:
                dt = t - last
                last = t
                if is_on:
                    tr.append(Message("note_on", note=pitch, velocity=vel, time=dt))
                else:
                    tr.append(Message("note_off", note=pitch, velocity=0, time=dt))
    
            mid.tracks.append(tr)
    
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mid.save(str(out_path))
        return True
# ---------------- Cleanup on close ----------------
    def closeEvent(self, event) -> None:
        # Stop players so Windows releases file handles
        try:
            self.midi_player.stop()
            self.audio_player.stop()
        except Exception:
            pass
    # Best-effort cleanup of session cache
        try:
            if getattr(self, "_session_cache_dir", None):
                shutil.rmtree(self._session_cache_dir, ignore_errors=True)
        except Exception:
            pass
        super().closeEvent(event)
