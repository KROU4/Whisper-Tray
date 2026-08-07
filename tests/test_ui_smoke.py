import os
import queue
import threading
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config_store import DEFAULT_CONFIG  # noqa: E402
from ui import ViewState, WhisperTrayUi  # noqa: E402


class FakeState:
    def __init__(self):
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["onboarding_complete"] = True
        self.tk_queue = queue.Queue()
        self.is_recording = threading.Event()
        self.is_file_transcribing = threading.Event()
        self.hotkey_listener = None
        self.file_transcriber = None

    def save_config(self, config):
        self.config = deepcopy(config)


def test_qt_worker_bridge_reports_result_and_error():
    app = QApplication.instance() or QApplication([])
    state = FakeState()
    view = WhisperTrayUi(state)
    state.tray_app = view
    view.ui_events.put(("transcript", "Smoke result."))
    state.tk_queue.put(("error", "Network unavailable"))

    view.drain_worker_events()

    assert view.last_result.toPlainText() == "Smoke result."
    assert view.status is ViewState.ERROR
    assert view.status_label.text() == "Network unavailable"
    state.tk_queue.put(("backend_switch", "Switching to local Whisper"))
    view.drain_worker_events()
    assert view.status is ViewState.PROCESSING
    assert view.status_label.text() == "Switching to local Whisper"
    state.is_recording.set()
    assert view.is_busy() is True
    view.poller.stop()
    view.hud.close()
    view.tray.hide()
    view.window.closeEvent = lambda event: event.accept()
    view.window.close()
    app.processEvents()
