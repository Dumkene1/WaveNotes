from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List
import numpy as np

from app.midi.model import NoteEvent
from app.state import Settings


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, sr: int, settings: Settings) -> List[NoteEvent]:
        raise NotImplementedError
