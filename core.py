"""Safe, UI-independent application contracts for WhisperTray."""

from __future__ import annotations

import threading
from enum import Enum


class Profile(str, Enum):
    PRIVACY = "privacy"
    SPEED = "speed"


class DictationStatus(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    INSERTED = "inserted"
    ERROR = "error"


class BackendError(Exception):
    """A safe, user-actionable transcription error."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class DictationStateMachine:
    """Serialises the recording lifecycle across hotkey and worker threads."""

    def __init__(self):
        self._lock = threading.RLock()
        self._status = DictationStatus.IDLE
        self._message = ""

    @property
    def status(self) -> DictationStatus:
        with self._lock:
            return self._status

    @property
    def message(self) -> str:
        with self._lock:
            return self._message

    def transition(self, expected: set[DictationStatus], target: DictationStatus, message: str = "") -> bool:
        with self._lock:
            if self._status not in expected:
                return False
            self._status, self._message = target, message
            return True

    def reset(self) -> None:
        with self._lock:
            self._status, self._message = DictationStatus.IDLE, ""
