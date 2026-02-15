# Drop-in replacement for app/ui/audio_separation_window.py
# Keeps backward-compatible attributes expected by MainWindow (e.g., lbl_model),
# while removing unnecessary UI (device selector, model row, wav output).

from __future__ import annotations

from typing import Optional, Dict, Any
from PySide6.QtWidgets import QProgressBar


from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFileDialog, QFormLayout, QComboBox, QSpinBox, QGroupBox, QWidget
)


class AudioSeparationWindow(QDialog):
    # MainWindow typically connects to this signal
    separationRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio Separation")
        self.setModal(False)

        self._input_path: Optional[str] = None

        root = QVBoxLayout(self)

        # --- (Compatibility) Keep lbl_model attribute even if we don't show it ---
        self.lbl_model = QLabel("htdemucs_6s")
        self.lbl_model.setToolTip("Demucs model used for separation (fixed).")
        self.lbl_model.hide()

        # --- Input/Output selectors ---
        io_box = QGroupBox("Files")
        io_layout = QFormLayout(io_box)

        self.txt_input = QLineEdit()
        self.txt_input.setReadOnly(True)

        btn_pick_input = QPushButton("Choose audio…")
        btn_pick_input.clicked.connect(self._pick_input)

        in_row = QWidget()
        in_row_l = QHBoxLayout(in_row)
        in_row_l.setContentsMargins(0, 0, 0, 0)
        in_row_l.addWidget(self.txt_input, 1)
        in_row_l.addWidget(btn_pick_input)

        self.txt_output_dir = QLineEdit()
        self.txt_output_dir.setReadOnly(True)

        btn_pick_output = QPushButton("Choose output folder…")
        btn_pick_output.clicked.connect(self._pick_output_dir)

        out_row = QWidget()
        out_row_l = QHBoxLayout(out_row)
        out_row_l.setContentsMargins(0, 0, 0, 0)
        out_row_l.addWidget(self.txt_output_dir, 1)
        out_row_l.addWidget(btn_pick_output)

        io_layout.addRow("Input audio:", in_row)
        io_layout.addRow("Output folder:", out_row)

        root.addWidget(io_box)

        # --- Settings (MP3 only; device removed) ---
        s_box = QGroupBox("Settings")
        s_form = QFormLayout(s_box)

        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["mp3"])
        self.cmb_format.setCurrentText("mp3")
        s_form.addRow("Output format:", self.cmb_format)

        self.cmb_bitrate = QComboBox()
        self.cmb_bitrate.addItems(["128", "192", "256", "320"])
        self.cmb_bitrate.setCurrentText("320")
        s_form.addRow("MP3 bitrate:", self.cmb_bitrate)

        self.spin_shifts = QSpinBox()
        self.spin_shifts.setRange(0, 10)
        self.spin_shifts.setValue(0)
        self.spin_shifts.setToolTip("Increase for slightly better quality at the cost of time.")
        s_form.addRow("Shifts:", self.spin_shifts)

        self.spin_segment = QSpinBox()
        self.spin_segment.setRange(0, 60)
        self.spin_segment.setValue(0)
        self.spin_segment.setToolTip("0 = default. Higher can reduce memory use; may affect speed/quality.")
        s_form.addRow("Segment (s):", self.spin_segment)

        root.addWidget(s_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)   # indeterminate / busy
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setVisible(False)
        root.addWidget(self.lbl_status)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_separate = QPushButton("Separate")
        self.btn_separate.clicked.connect(self._on_separate_clicked)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)

        btn_row.addWidget(self.btn_separate)
        btn_row.addWidget(self.btn_close)

        root.addLayout(btn_row)

        self.resize(640, 260)

    # ---- Public API (used by MainWindow in some versions) ----
    def set_input_path(self, path: str):
        self._input_path = path
        self.txt_input.setText(path)

    def _refresh_stems_from_folder(self, folder: str = ""):
        # Compatibility no-op: MainWindow may call this after separation.
        return

    # ---- Internal helpers ----
    def _pick_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose audio file", "", "Audio Files (*.mp3 *.wav *.flac *.m4a *.ogg *.aac);;All Files (*)"
        )
        if path:
            self.set_input_path(path)

    def _pick_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.txt_output_dir.setText(folder)

    def _on_separate_clicked(self):
        in_path = (self._input_path or "").strip()
        out_dir = self.txt_output_dir.text().strip()

        if not in_path or not out_dir:
            return

        payload: Dict[str, Any] = {
            "input_path": in_path,
            "out_dir": out_dir,
            "model": "htdemucs_6s",
            "output_format": "mp3",
            "mp3_bitrate": self.cmb_bitrate.currentText().strip(),
            "device": "cpu",
            "shifts": int(self.spin_shifts.value()),
            "segment": int(self.spin_segment.value()),
        }

        self.separationRequested.emit(payload)
        
    def set_busy(self, busy: bool, text: str = ""):
        self.progress.setVisible(busy)
        self.lbl_status.setVisible(busy)
        self.lbl_status.setText(text or ("Separating…" if busy else ""))
        self.btn_separate.setEnabled(not busy)

