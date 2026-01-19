from __future__ import annotations
from pathlib import Path
import tempfile
import shutil


class SessionCache:
    """
    Creates a temporary cache folder for this app run.
    Deleted when you call cleanup() (we'll call it on app exit).
    """
    def __init__(self, prefix: str = "wavenotes_") -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.dir = Path(self._tmp.name)

    def cleanup(self) -> None:
        # TemporaryDirectory cleanup is enough, but keep explicit for clarity.
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def clear(self) -> None:
        # Clear files inside the session cache without deleting the folder itself.
        if self.dir.exists():
            for p in self.dir.iterdir():
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
