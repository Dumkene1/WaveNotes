from __future__ import annotations
from pathlib import Path


def project_root() -> Path:
    # app/ is one level under wavenotes/
    return Path(__file__).resolve().parents[1]


ROOT_DIR = project_root()
ASSETS_DIR = ROOT_DIR / "assets"
FFMPEG_DIR = ASSETS_DIR / "ffmpeg"
DEFAULT_EXPORT_DIR = ROOT_DIR / "exports"
DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
