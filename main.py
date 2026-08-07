"""WhisperTray application entry point.

Qt owns the sole GUI event loop. Recording and transcription stay in their
dedicated worker threads and communicate with it through the state contract.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path

from config_store import ConfigStore, app_data_dir
from core import DictationStateMachine
from logging_setup import configure_logging


def _configure_cuda_path() -> None:
    for version in ("12.0", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6"):
        directory = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA") / f"v{version}" / "bin"
        if directory.exists():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            return


class AppState:
    """Thread-safe, UI-independent state shared by workers and the Qt adapter."""

    def __init__(self) -> None:
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.dictation_state = DictationStateMachine()
        self.operation_lock = threading.RLock()
        self.is_recording = threading.Event()
        self.is_file_transcribing = threading.Event()
        # Compatibility bridge for existing workers; Qt consumes this queue.
        self.tk_queue: queue.Queue = queue.Queue()
        self.tray_app = None
        self.hotkey_listener = None
        self.file_transcriber = None
        self.hotkey_thread = None
        self.on_transcript = None

    def save_config(self, config: dict) -> None:
        """Persist atomically before exposing a changed configuration to workers."""
        self.config_store.save(config)
        self.config = self.config_store.load()


def main() -> int:
    _configure_cuda_path()
    configure_logging(app_data_dir())
    logger = logging.getLogger(__name__)
    state = AppState()

    from file_transcriber import FileTranscriptionWorker
    from hotkey import HotkeyListener

    listener = HotkeyListener(state)
    state.hotkey_listener = listener
    state.file_transcriber = FileTranscriptionWorker(state)
    from ui import run_qt

    logger.info("WhisperTray started: profile=%s, hotkey=%s", state.config.get("profile"), state.config.get("hotkey"))
    return run_qt(state)


if __name__ == "__main__":
    raise SystemExit(main())
