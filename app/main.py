from __future__ import annotations
import sys
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
import os
from pathlib import Path


def main() -> int:
    # Windows taskbar icon consistency
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WaveNotes.WaveNotes")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # <-- add this    
    
    def configure_torch_cache():
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WaveNotes"
        torch_home = base / "torch"
        torch_home.mkdir(parents=True, exist_ok=True)
        os.environ["TORCH_HOME"] = str(torch_home)
    configure_torch_cache()
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
