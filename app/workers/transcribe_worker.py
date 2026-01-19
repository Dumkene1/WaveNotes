from __future__ import annotations
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QThread

from app.pipeline.analyze_audio import analyze_audio
from app.state import Settings, AnalysisSession


class AnalyzeWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)  # AnalysisSession
    error = Signal(str)

    def __init__(self, input_path: Path, settings: Settings, cache_dir: Path) -> None:
        super().__init__()
        self.input_path = input_path
        self.settings = settings
        self.cache_dir = cache_dir

    @Slot()
    def run(self) -> None:
        try:
            session = analyze_audio(
                self.input_path,
                self.settings,
                self.cache_dir,
                progress=lambda p, m: self.progress.emit(p, m),
            )
            self.finished.emit(session)
        except Exception as e:
            self.error.emit(str(e))


def start_analyze_worker(input_path: Path, settings: Settings, cache_dir: Path):
    thread = QThread()
    worker = AnalyzeWorker(input_path, settings, cache_dir)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    worker.error.connect(thread.quit)
    worker.error.connect(worker.deleteLater)

    thread.start()
    return thread, worker
