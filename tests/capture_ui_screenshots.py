"""Create deterministic headless screenshots of primary WhisperTray UI states.

Usage:
    $env:QT_QPA_PLATFORM='offscreen'; python tests/capture_ui_screenshots.py

All images go to ``artifacts/screenshots``. The scenario uses an explicitly
offline mock transcript and a generated sine-wave WAV; it never calls Groq or
loads a Whisper model.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from config_store import DEFAULT_CONFIG
from ui import DiagnosticsDialog, SettingsDialog, ViewState, WhisperTrayUi


class ScreenshotState:
    def __init__(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config.update({"onboarding_complete": True, "ui_language": "en"})
        self.tk_queue: queue.Queue = queue.Queue()
        self.is_recording = threading.Event()
        self.is_file_transcribing = threading.Event()
        self.hotkey_listener = None
        self.file_transcriber = None

    def save_config(self, config: dict) -> None:
        self.config = deepcopy(config)


def capture(widget, path: Path) -> None:
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save screenshot: {path}")


def main() -> int:
    destination = Path(__file__).resolve().parents[1] / "artifacts" / "screenshots"
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    view = WhisperTrayUi(ScreenshotState())
    view.window.resize(460, 390)
    view.window.show()
    app.processEvents()

    for status in ViewState:
        message = "Offline backend unavailable" if status is ViewState.ERROR else None
        view.set_state(status, message)
        if status is ViewState.INSERTED:
            view.last_result.setPlainText("Offline mock transcription.")
        app.processEvents()
        capture(view.window, destination / f"main-{status.value}.png")
        if status is not ViewState.IDLE:
            capture(view.hud, destination / f"hud-{status.value}.png")

    view.set_state(ViewState.IDLE)
    settings = SettingsDialog(view)
    settings.show()
    app.processEvents()
    capture(settings, destination / "settings.png")
    settings.close()

    onboarding = SettingsDialog(view, onboarding=True)
    onboarding.show()
    app.processEvents()
    capture(onboarding, destination / "onboarding.png")
    onboarding.close()

    diagnostics = DiagnosticsDialog(view)
    diagnostics.show()
    app.processEvents()
    capture(diagnostics, destination / "diagnostics.png")
    diagnostics.close()

    tray_menu = view.tray.contextMenu()
    tray_menu.popup(QPoint(40, 40))
    app.processEvents()
    capture(tray_menu, destination / "tray-menu.png")
    tray_menu.close()

    view.poller.stop()
    view.hud.close()
    view.tray.hide()
    view.window.closeEvent = lambda event: event.accept()
    view.window.close()
    app.processEvents()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
