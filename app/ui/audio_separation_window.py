from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QComboBox,
    QCheckBox,
    QListWidget,
    QProgressBar,
    QSpinBox,
)


class AudioSeparationWindow(QDialog):
    """WaveNotes — Audio Separation (Demucs)

    This window ONLY handles audio separation configuration + triggering separation.
    It does not run transcription directly.

    MainWindow is expected to listen to `separationRequested` and run the actual separation
    in a worker thread/process.

    Payload keys emitted:
      - input_path: str
      - output_dir: str
      - overwrite: bool
      - output_format: str ("wav" or "mp3")
      - flat_naming: bool  (rename outputs to <base>_<stem>.<ext> in output_dir)
      - model: str         (fixed to "htdemucs_6s")
      - device: str        ("auto" | "cpu")  [optional; MainWindow may ignore]
      - mp3_bitrate: int   (kbps; only relevant when output_format == "mp3") [optional]
      - segment: int       (seconds; optional)
      - shifts: int        (optional)
    """

    separationRequested = Signal(dict)

    _AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("WaveNotes — Audio Separation")
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)

        # --- Input / Output
        io_box = QGroupBox("Input / Output")
        io_form = QFormLayout(io_box)

        self.ed_input = QLineEdit()
        self.ed_input.setPlaceholderText("Drop an audio file here, or click Browse…")
        self.btn_browse_input = QPushButton("Browse…")
        row_in = QHBoxLayout()
        row_in.addWidget(self.ed_input, 1)
        row_in.addWidget(self.btn_browse_input)
        io_form.addRow("Audio file:", row_in)

        self.ed_out = QLineEdit()
        self.ed_out.setPlaceholderText("Select a folder to save stems")
        self.btn_browse_out = QPushButton("Browse…")
        row_out = QHBoxLayout()
        row_out.addWidget(self.ed_out, 1)
        row_out.addWidget(self.btn_browse_out)
        io_form.addRow("Output folder:", row_out)

        root.addWidget(io_box)

        # --- Settings
        s_box = QGroupBox("Settings")
        s_form = QFormLayout(s_box)

        # Model is fixed (as you requested)
        self.lbl_model = QLabel("htdemucs_6s")
        self.lbl_model.setToolTip("Demucs model used for separation (fixed).")
        s_form.addRow("Separation model:", self.lbl_model)

        self.cmb_device = QComboBox()
        self.cmb_device.addItems(["auto", "cpu"])
        self.cmb_device.setToolTip("Leave on auto unless you know you want CPU-only mode.")
        s_form.addRow("Device:", self.cmb_device)

        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["wav", "mp3"])
        s_form.addRow("Output format:", self.cmb_format)

        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(96, 320)
        self.spin_bitrate.setSingleStep(32)
        self.spin_bitrate.setValue(320)
        self.spin_bitrate.setToolTip("Used only when Output format is MP3.")
        s_form.addRow("MP3 bitrate (kbps):", self.spin_bitrate)

        self.spin_segment = QSpinBox()
        self.spin_segment.setRange(0, 300)
        self.spin_segment.setValue(0)
        self.spin_segment.setToolTip("Optional: Demucs segment length in seconds. 0 = default.")
        s_form.addRow("Segment (sec):", self.spin_segment)

        self.spin_shifts = QSpinBox()
        self.spin_shifts.setRange(0, 10)
        self.spin_shifts.setValue(0)
        self.spin_shifts.setToolTip("Optional: number of random shifts. 0 = default.")
        s_form.addRow("Shifts:", self.spin_shifts)

        self.chk_overwrite = QCheckBox("Overwrite existing stem files")
        self.chk_overwrite.setChecked(False)
        s_form.addRow("", self.chk_overwrite)

        self.chk_flat = QCheckBox("Use WaveNotes naming: audioX_voice/drums/bass/piano/guitar/other.<ext>")
        self.chk_flat.setChecked(True)
        s_form.addRow("", self.chk_flat)

        root.addWidget(s_box)

        # --- Actions
        btn_row = QHBoxLayout()
        self.btn_separate = QPushButton("Separate")
        self.btn_close = QPushButton("Close")
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_separate)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        # --- Status + Progress
        self.progress_sep = QProgressBar()
        self.progress_sep.setRange(0, 0)  # indeterminate while running
        self.progress_sep.setVisible(False)
        root.addWidget(self.progress_sep)

        self.lbl_sep_status = QLabel("")
        self.lbl_sep_status.setWordWrap(True)
        root.addWidget(self.lbl_sep_status)

        # --- Output preview
        out_box = QGroupBox("Generated stems in output folder")
        out_l = QVBoxLayout(out_box)
        self.list_stems = QListWidget()
        out_l.addWidget(self.list_stems)
        root.addWidget(out_box)

        # Wire up
        self.btn_browse_input.clicked.connect(self._browse_input)
        self.btn_browse_out.clicked.connect(self._browse_out)
        self.btn_separate.clicked.connect(self._emit_separate)
        self.btn_close.clicked.connect(self.close)
        self.cmb_format.currentTextChanged.connect(self._sync_format_ui)

        self._sync_format_ui()

    # --- Drag & drop

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    p = Path(url.toLocalFile())
                    if p.suffix.lower() in self._AUDIO_EXTS:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        md = event.mimeData()
        if not md.hasUrls():
            event.ignore()
            return

        for url in md.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.suffix.lower() not in self._AUDIO_EXTS:
                continue
            self.ed_input.setText(str(p))
            self.lbl_sep_status.setText("Loaded input from drop.")
            event.acceptProposedAction()
            return

        event.ignore()

    # --- External helpers

    def set_input_path(self, path: str) -> None:
        if path:
            self.ed_input.setText(path)

    # --- UI actions

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio file",
            "",
            "Audio files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg);;All files (*.*)",
        )
        if path:
            self.ed_input.setText(path)

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", "")
        if d:
            self.ed_out.setText(d)

    def _sync_format_ui(self) -> None:
        is_mp3 = self.cmb_format.currentText().strip().lower() == "mp3"
        self.spin_bitrate.setEnabled(is_mp3)

    def _emit_separate(self) -> None:
        input_path = self.ed_input.text().strip()
        out_dir = self.ed_out.text().strip()

        if not input_path:
            self.lbl_sep_status.setText("❌ Choose an audio file first (or drag & drop one here).")
            return
        if not out_dir:
            self.lbl_sep_status.setText("❌ Choose an output folder first.")
            return

        payload = {
            "input_path": input_path,
            "output_dir": out_dir,
            "overwrite": bool(self.chk_overwrite.isChecked()),
            "model": "htdemucs_6s",
            "device": self.cmb_device.currentText().strip(),
            "output_format": self.cmb_format.currentText().strip().lower(),
            "mp3_bitrate": int(self.spin_bitrate.value()),
            "segment": int(self.spin_segment.value()),
            "shifts": int(self.spin_shifts.value()),
            "flat_naming": bool(self.chk_flat.isChecked()),
        }

        self.lbl_sep_status.setText("Separating…")
        self.progress_sep.setVisible(True)
        self.separationRequested.emit(payload)

    def _refresh_stems_from_folder(self) -> None:
        """Called by MainWindow after separation finishes."""
        self.list_stems.clear()
        out_dir = self.ed_out.text().strip()
        if not out_dir:
            return
        p = Path(out_dir)
        if not p.exists():
            return

        files = sorted([f for f in p.glob("*.*") if f.suffix.lower() in (".wav", ".mp3")])
        for f in files:
            self.list_stems.addItem(str(f.name))