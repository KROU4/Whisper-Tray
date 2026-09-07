"""WhisperTray application entry point.

Qt owns the sole GUI event loop. Recording and transcription stay in their
dedicated worker threads and communicate with it through the state contract.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import re
import sys
import threading
from pathlib import Path

from config_store import ConfigStore, app_data_dir
from core import DictationStateMachine
from logging_setup import configure_logging

_SPAWN_CODE = re.compile(
    r"from multiprocessing\.spawn import spawn_main;\s*spawn_main\((?P<arguments>[^()]*)\)"
)
_RESOURCE_TRACKER_CODE = re.compile(
    r"from multiprocessing\.resource_tracker import main;\s*main\((?P<fd>\d+)\)"
)
_SPAWN_ARGUMENT = re.compile(r"(?P<name>parent_pid|pipe_handle|tracker_fd)=(?P<value>-?\d+)")


def _run_multiprocessing_bootstrap(argv: list[str] | None = None) -> bool:
    """Handle the two ``-c`` commands a Briefcase POSIX stub cannot execute.

    The native launcher always starts this app module, even when Python's
    multiprocessing passes ``-c``. Only exact standard-library bootstrap forms
    are accepted; arbitrary command strings are never evaluated.
    """
    arguments = list(sys.argv if argv is None else argv)
    command_index = 1
    # multiprocessing reproduces the embedded interpreter's flags before -c.
    # Briefcase forwards them as app arguments instead of consuming them.
    while command_index < len(arguments) and arguments[command_index] != "-c":
        flag = arguments[command_index]
        if re.fullmatch(r"-(?:[dBSvbqO]+|I|E|s|P|W.+)", flag):
            command_index += 1
        elif flag == "-X" and command_index + 1 < len(arguments):
            if arguments[command_index + 1].startswith("-"):
                return False
            command_index += 2
        else:
            return False
    if command_index + 1 >= len(arguments):
        return False
    code = arguments[command_index + 1].strip()
    tracker_match = _RESOURCE_TRACKER_CODE.fullmatch(code)
    if tracker_match is not None:
        from multiprocessing.resource_tracker import main as resource_tracker_main

        resource_tracker_main(int(tracker_match.group("fd")))
        return True

    spawn_match = _SPAWN_CODE.fullmatch(code)
    if spawn_match is None or "--multiprocessing-fork" not in arguments[command_index + 2:]:
        return False
    parsed = {}
    raw_arguments = spawn_match.group("arguments").strip()
    if raw_arguments:
        for item in raw_arguments.split(","):
            item_match = _SPAWN_ARGUMENT.fullmatch(item.strip())
            if item_match is None or item_match.group("name") in parsed:
                return False
            parsed[item_match.group("name")] = int(item_match.group("value"))
    if "pipe_handle" not in parsed:
        return False

    from multiprocessing.spawn import spawn_main

    sys.argv = [arguments[0], "--multiprocessing-fork"] + [f"{key}={value}" for key, value in parsed.items()]
    spawn_main(**parsed)
    return True


def _configure_multiprocessing() -> None:
    """Enable frozen-process spawning for the Briefcase Windows launcher."""
    executable = Path(sys.executable).stem.lower()
    if os.name == "nt" and executable == "whispertray" and not getattr(sys, "frozen", False):
        # Briefcase's app stub is the process executable but does not set the
        # marker multiprocessing uses to generate --multiprocessing-fork.
        sys.frozen = True
    multiprocessing.freeze_support()


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
        self.jobs = None
        self.hotkey_thread = None
        self.on_transcript = None

    def save_config(self, config: dict) -> None:
        """Persist atomically before exposing a changed configuration to workers."""
        self.config_store.save(config)
        self.config = self.config_store.load()


def main() -> int:
    if _run_multiprocessing_bootstrap():
        return 0
    _configure_multiprocessing()
    _configure_cuda_path()
    configure_logging(app_data_dir())
    logger = logging.getLogger(__name__)
    state = AppState()

    from file_transcriber import FileTranscriptionWorker
    from hotkey import HotkeyListener
    from jobs import JobController

    state.jobs = JobController(state)
    listener = HotkeyListener(state)
    state.hotkey_listener = listener
    state.file_transcriber = FileTranscriptionWorker(state)
    try:
        from ui import run_qt

        logger.info(
            "WhisperTray started: profile=%s, hotkey=%s",
            state.config.get("profile"),
            state.config.get("hotkey"),
        )
        return run_qt(state, force_show="--show" in sys.argv[1:])
    finally:
        # Normal UI shutdown is idempotent; this also covers import/startup
        # failures and unexpected event-loop exits.
        try:
            listener.shutdown()
        except Exception:
            logger.exception("Runtime shutdown failed")
        state.jobs.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
