"""Headless UI/E2E coverage using a clearly-marked offline mock backend.

The mock validates a generated WAV and emits a deterministic transcript.  It
does not load Whisper or make a network request, so this is a UI pipeline test,
not an ASR-accuracy test.
"""
from __future__ import annotations

import os
import queue
import threading
import wave
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSize, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
)

from config_store import DEFAULT_CONFIG  # noqa: E402
from history_store import HistoryStore  # noqa: E402
from ui import (  # noqa: E402
    HistoryDialog,
    HotkeyCaptureDialog,
    SettingsDialog,
    ViewState,
    WhisperTrayUi,
    hotkey_from_key_event,
    should_show_main_window,
)


class FakeState:
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


def make_synthetic_voice_wav(path: Path) -> Path:
    """Create a short speech-like tone fixture without committing audio data."""
    sample_rate = 16_000
    duration = 0.35
    samples = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
    waveform = 0.18 * np.sin(2 * np.pi * 220 * samples) + 0.08 * np.sin(2 * np.pi * 440 * samples)
    pcm = np.asarray(waveform * 32767, dtype="<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return path


class MockLocalWavBackend:
    """Offline mock backend for E2E flow; intentionally not a Whisper substitute."""
    transcript = "Offline mock transcription."

    def transcribe(self, wav_path: Path) -> str:
        with wave.open(str(wav_path), "rb") as source:
            assert source.getframerate() == 16_000
            assert source.getnchannels() == 1
            assert source.getnframes() > 0
        return self.transcript


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qt_app):
    result = WhisperTrayUi(FakeState())
    result.window.show()
    qt_app.processEvents()
    yield result
    result.poller.stop()
    result._history_timer.stop()
    result.hud.close()
    result.tray.hide()
    result.window.closeEvent = lambda event: event.accept()
    result.window.close()
    qt_app.processEvents()


def test_state_machine_updates_window_hud_and_tray(view, qt_app):
    expected = {
        ViewState.IDLE: "Ready for dictation",
        ViewState.RECORDING: "Recording",
        ViewState.PROCESSING: "Transcribing…",
        ViewState.INSERTED: "Text inserted",
        ViewState.ERROR: "Offline error",
    }
    for state, text in expected.items():
        view.set_state(state, "Offline error" if state is ViewState.ERROR else None)
        qt_app.processEvents()
        assert view.status is state
        assert view.status_label.text() == text
        assert view.tray.toolTip().endswith(text)
        assert view.hud.isVisible() is (state is not ViewState.IDLE)


def test_global_space_hotkey_does_not_also_activate_focused_record_button(view, qt_app):
    activated = threading.Event()

    class Listener:
        def on_hotkey(self):
            activated.set()

    view.state.config["hotkey"] = "ctrl+shift+space"
    view.state.hotkey_listener = Listener()
    view.action_button.setFocus()
    QTest.keyClick(view.action_button, Qt.Key_Space, Qt.ControlModifier | Qt.ShiftModifier)
    qt_app.processEvents()

    assert not activated.wait(0.1)

    QTest.keyClick(view.action_button, Qt.Key_Space)
    assert activated.wait(1)


def test_offline_mock_wav_e2e_reaches_inserted_with_no_network(view, qt_app, tmp_path):
    wav = make_synthetic_voice_wav(tmp_path / "synthetic_voice.wav")
    backend = MockLocalWavBackend()

    view.state.tk_queue.put(("show_hud",))
    view.drain_worker_events()
    assert view.status is ViewState.RECORDING

    view.state.tk_queue.put(("processing",))
    view.drain_worker_events()
    assert view.status is ViewState.PROCESSING

    # Explicitly offline/mock: no Groq client, no local Whisper model, no network.
    view.ui_events.put(("transcript", backend.transcribe(wav)))
    view.state.tk_queue.put(("inserted",))
    view.drain_worker_events()
    qt_app.processEvents()

    assert view.status is ViewState.INSERTED
    assert view.last_result.toPlainText() == MockLocalWavBackend.transcript
    assert wav.stat().st_size > 44


def test_settings_save_updates_privacy_profile_without_secret(view, qt_app, monkeypatch):
    dialog = SettingsDialog(view)
    monkeypatch.setattr("platform_integration.parse_hotkey", lambda value: value)
    dialog.profile.setCurrentIndex(dialog.profile.findData("privacy"))
    dialog.hotkey.setText("ctrl+space")
    dialog.save()
    qt_app.processEvents()

    assert view.state.config["profile"] == "privacy"
    assert view.state.config["transcription_backend"] == "local"
    assert "groq_api_key" not in view.state.config


def test_settings_save_failure_does_not_show_raw_exception(view, monkeypatch):
    messages = []
    monkeypatch.setattr("platform_integration.parse_hotkey", lambda value: value)
    monkeypatch.setattr(view, "save_config", lambda _config: (_ for _ in ()).throw(OSError("secret-value")))
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, message: messages.append(message))
    dialog = SettingsDialog(view)

    dialog.save()

    assert messages == ["Settings could not be saved safely. The previous settings were not changed."]
    assert "secret-value" not in messages[0]


def test_groq_key_test_uses_stored_key_bounded_client_and_safe_error(view, qt_app, monkeypatch):
    calls = []
    messages = []

    class Models:
        @staticmethod
        def list():
            raise RuntimeError("stored-key-must-not-appear")

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.models = Models()

    monkeypatch.setattr("credentials.CredentialStore.get_groq_key", lambda _self: "stored-key-must-not-appear")
    monkeypatch.setattr("groq.Groq", Client)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: messages.append(message))
    dialog = SettingsDialog(view)
    dialog.show()
    dialog.groq_key.clear()

    dialog.test_groq_key()
    QTest.qWait(180)
    qt_app.processEvents()

    assert calls == [{"api_key": "stored-key-must-not-appear", "timeout": 60.0, "max_retries": 0}]
    assert messages == ["The Groq key could not be verified. Check the key and your internet connection."]
    assert "stored-key-must-not-appear" not in messages[0]
    dialog.close()


def test_groq_key_poll_does_not_show_message_after_settings_close(view, qt_app, monkeypatch):
    release = threading.Event()
    messages = []

    class Models:
        @staticmethod
        def list():
            release.wait(1)

    class Client:
        def __init__(self, **_kwargs):
            self.models = Models()

    monkeypatch.setattr("groq.Groq", Client)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append("shown"))
    dialog = SettingsDialog(view)
    dialog.show()
    dialog.groq_key.setText("temporary-key")
    dialog.test_groq_key()
    dialog.reject()
    release.set()
    QTest.qWait(180)
    qt_app.processEvents()

    assert messages == []


def test_microphone_poll_does_not_touch_closed_settings(view, qt_app, monkeypatch):
    import sounddevice as sd

    started = threading.Event()
    release = threading.Event()
    messages = []
    monkeypatch.setattr(sd, "check_input_settings", lambda **_kwargs: None)
    monkeypatch.setattr(sd, "rec", lambda *_args, **_kwargs: started.set() or np.zeros((80_000, 1)))
    monkeypatch.setattr(sd, "wait", lambda: release.wait(1))
    monkeypatch.setattr(sd, "stop", release.set)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append("shown"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append("shown"))
    dialog = SettingsDialog(view)
    dialog.show()

    dialog.test_microphone()
    assert started.wait(1)
    dialog.reject()
    QTest.qWait(150)
    qt_app.processEvents()

    assert messages == []


def test_history_clear_poll_does_not_touch_closed_settings(view, qt_app, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    messages = []

    def clear(_store):
        started.set()
        release.wait(1)

    monkeypatch.setattr("history_store.HistoryStore.clear", clear)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append("shown"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append("shown"))
    dialog = SettingsDialog(view)
    dialog.show()

    dialog.clear_history()
    assert started.wait(1)
    dialog.reject()
    release.set()
    QTest.qWait(120)
    qt_app.processEvents()

    assert messages == []


def test_legacy_model_prepare_poll_does_not_touch_closed_settings(view, qt_app, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    messages = []

    def prepare(_transcriber):
        started.set()
        release.wait(1)

    monkeypatch.setattr("transcriber.Transcriber._ensure_model", prepare)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: messages.append("shown"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append("shown"))
    dialog = SettingsDialog(view)
    dialog.show()

    dialog.prepare_local_model()
    assert started.wait(1)
    dialog.reject()
    release.set()
    QTest.qWait(250)
    qt_app.processEvents()

    assert messages == []


@pytest.mark.parametrize(
    ("notice", "phrase"),
    (
        ("config_recovered", "backup copy was saved"),
        ("config_backup_failed", "was not overwritten"),
        ("config_unavailable", "is unavailable and was not overwritten"),
    ),
)
def test_configuration_recovery_notice_is_localized_and_shown_once(view, monkeypatch, notice, phrase):
    messages = []
    view.state.config_store = type("Store", (), {"recovery_notice": notice})()
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: messages.append(message))
    view.window.show()

    view.show_recovery_notice()
    view.show_recovery_notice()

    assert len(messages) == 1
    assert phrase in messages[0]


def test_hotkey_capture_translates_ctrl_space_to_machine_format(qt_app):
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier, " ")
    assert hotkey_from_key_event(event) == "ctrl+space"


def test_settings_hotkey_is_changed_through_capture_button_not_manual_input(view):
    dialog = SettingsDialog(view)

    assert dialog.hotkey.isReadOnly()
    assert dialog.hotkey.accessibleName() == "Hotkey"
    assert dialog.change_hotkey_button.text() == "Change"
    assert dialog.change_hotkey_button.isEnabled()


def test_settings_change_button_saves_captured_hotkey_immediately(view, qt_app, monkeypatch):
    monkeypatch.setattr("platform_integration.parse_hotkey", lambda value: value)

    def capture(dialog):
        dialog.hotkey = "ctrl+space"
        return QDialog.Accepted

    monkeypatch.setattr(HotkeyCaptureDialog, "exec", capture)
    dialog = SettingsDialog(view)
    dialog.change_hotkey_button.click()
    qt_app.processEvents()

    assert view.state.config["hotkey"] == "ctrl+space"
    assert dialog.hotkey.text() == "Ctrl + Space"
    assert dialog.hotkey_feedback.isVisible() is False  # Parent dialog is not shown in this test.
    assert "Saved" in dialog.hotkey_feedback.text()


def test_cancelled_hotkey_capture_restores_global_registration(view, monkeypatch):
    calls = []

    class Listener:
        def suspend_hotkey(self):
            calls.append("suspend")
            return True

        def resume_hotkey(self):
            calls.append("resume")

    view.state.hotkey_listener = Listener()
    monkeypatch.setattr(HotkeyCaptureDialog, "exec", lambda _dialog: QDialog.Rejected)
    dialog = SettingsDialog(view)
    dialog.change_hotkey_button.click()

    assert calls == ["suspend", "resume"]
    assert view.state.config["hotkey"] == "win+alt"


def test_choosing_same_hotkey_restores_global_registration(view, monkeypatch):
    calls = []

    class Listener:
        def suspend_hotkey(self):
            calls.append("suspend")
            return True

        def resume_hotkey(self):
            calls.append("resume")

    def capture(dialog):
        dialog.hotkey = "win+alt"
        return QDialog.Accepted

    view.state.hotkey_listener = Listener()
    monkeypatch.setattr(HotkeyCaptureDialog, "exec", capture)
    dialog = SettingsDialog(view)
    dialog.change_hotkey_button.click()

    assert calls == ["suspend", "resume"]
    assert view.state.config["hotkey"] == "win+alt"


def test_new_hotkey_and_failed_save_both_restore_registration(view, monkeypatch):
    calls = []

    class Listener:
        def suspend_hotkey(self):
            calls.append("suspend")
            return True

        def reload_hotkey(self, hotkey, mode):
            calls.append(("reload", hotkey, mode))

        def resume_hotkey(self):
            calls.append("resume")

    def capture(dialog):
        dialog.hotkey = "ctrl+space"
        return QDialog.Accepted

    view.state.hotkey_listener = Listener()
    monkeypatch.setattr(HotkeyCaptureDialog, "exec", capture)
    SettingsDialog(view).change_hotkey_button.click()
    assert calls == ["suspend", ("reload", "ctrl+space", "toggle"), "resume"]

    calls.clear()
    monkeypatch.setattr(view, "save_config", lambda _config: (_ for _ in ()).throw(OSError("read only")))
    SettingsDialog(view).change_hotkey_button.click()
    assert calls == ["suspend", "resume"]


def test_job_events_ignore_stale_result_and_keep_errors_visible(view, qt_app):
    class Jobs:
        current_job_id = "new"
        busy = True

        def recording_snapshot(self):
            return {"elapsed": 65, "level": 0.42, "silent_seconds": 6}

    view.state.jobs = Jobs()
    view.state.tk_queue.put(
        ("job", {"job_id": "old", "status": "error", "kind": "file", "code": "save_failed", "text": "stale"})
    )
    view.state.tk_queue.put(("job", {"job_id": "new", "status": "recording"}))
    view.drain_worker_events()
    qt_app.processEvents()

    assert view.last_result.toPlainText() == ""
    assert view.status is ViewState.RECORDING
    assert view.audio_level.value() == 42
    assert "01:05" in view.detail_label.text()
    assert "No speech" in view.detail_label.text()

    view.state.tk_queue.put(
        ("job", {"job_id": "new", "status": "error", "code": "empty_audio", "retry_available": True})
    )
    view.drain_worker_events()
    assert view.status is ViewState.ERROR
    assert view.retry_button.isVisible() is True
    assert "five minutes" in view.status_label.text()


def test_recording_can_be_cancelled_and_silence_detail_wraps_at_minimum_width(view, qt_app):
    class Jobs:
        current_job_id = "recording-1"
        busy = True

        def recording_snapshot(self):
            return {"elapsed": 9, "level": 0, "silent_seconds": 9}

    view.state.jobs = Jobs()
    view.window.resize(440, view.window.height())
    view.state.tk_queue.put(("job", {"job_id": "recording-1", "status": "recording", "kind": "dictation"}))
    view.drain_worker_events()
    qt_app.processEvents()

    assert view.cancel_job_button.isVisible()
    assert view.detail_label.wordWrap()
    assert view.status_label.wordWrap()
    # Native fonts can fit this message on one line. Check for clipping using
    # Qt's layout requirement at the actual width, rather than a line count.
    assert view.detail_label.height() >= view.detail_label.heightForWidth(view.detail_label.width())


def test_two_fast_file_jobs_show_only_latest_and_ignore_stale_preparing(view):
    class Jobs:
        current_job_id = "second"
        busy = False

    view.state.jobs = Jobs()
    events = (
        {"job_id": "first", "status": "processing", "kind": "file"},
        {"job_id": "first", "status": "idle", "kind": "file", "text": "First result"},
        {"job_id": "second", "status": "processing", "kind": "file"},
        {"job_id": "second", "status": "idle", "kind": "file", "text": "Second result"},
        {"job_id": "first", "status": "preparing", "kind": "model"},
    )
    for payload in events:
        view.state.tk_queue.put(("job", payload))

    view.drain_worker_events()

    assert view.current_job_id == "second"
    assert view.current_job_kind == "file"
    assert view.last_result.toPlainText() == "Second result"
    assert view.status is ViewState.IDLE


def test_job_controller_routes_actions_and_file_local_choice(view, tmp_path, monkeypatch):
    calls = []

    class Jobs:
        current_job_id = None
        busy = False

        def toggle_recording(self):
            calls.append("toggle")
            return True

        def submit_file(self, path, use_local=False):
            calls.append(("file", path, use_local))
            return True

        def cancel(self):
            calls.append("cancel")

        def retry(self):
            calls.append("retry")
            return True

    unsupported = tmp_path / "audio.xyz"
    unsupported.write_bytes(b"audio")
    view.state.config["profile"] = "speed"
    view.state.jobs = Jobs()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(unsupported), ""))

    def accept_local(prompt):
        next(button for button in prompt.buttons() if button.text() == "Process locally").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", accept_local)

    view.toggle_recording()
    view.transcribe_file()
    view.cancel_job()
    view.retry_job()

    assert calls == ["toggle", ("file", str(unsupported), True), "cancel", "retry"]


def test_file_job_result_is_visible_and_copyable(view, qt_app, tmp_path):
    output = tmp_path / "result.txt"
    view.state.tk_queue.put(
        ("job", {"job_id": "file-1", "status": "inserted", "kind": "file", "text": "File result", "output_path": str(output)})
    )
    view.drain_worker_events()
    view.copy_result()
    qt_app.processEvents()

    assert view.last_result.toPlainText() == "File result"
    assert view.copy_button.isEnabled()
    assert view.save_button.isEnabled()
    assert view.open_folder_button.isVisible()
    assert QApplication.clipboard().text() == "File result"


def test_job_result_is_appended_to_history_once(view, monkeypatch):
    appended = []

    class Store:
        def append(self, text, retention):
            appended.append((text, retention))

    monkeypatch.setattr("history_store.HistoryStore", Store)
    view.state.config["history"] = {"enabled": True, "retention_days": 30}
    view.state.tk_queue.put(
        ("job", {"job_id": "history-1", "status": "idle", "kind": "file", "text": "Saved once"})
    )
    view.drain_worker_events()
    with view._history_threads_lock:
        workers = list(view._history_threads)
    for worker in workers:
        worker.join(1)

    assert appended == [("Saved once", 30)]


def test_open_history_loads_and_searches_real_isolated_store(view, qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("WHISPERTRAY_DATA_DIR", str(tmp_path / "profile"))
    store = HistoryStore()
    store.append("Alpha transcript", 30)
    store.append("Needle transcript", 30)
    view.state.config["history"] = {"enabled": True, "retention_days": 30}
    observed = {}

    def inspect_initial():
        dialog = QApplication.activeModalWidget()
        observed["is_history_dialog"] = isinstance(dialog, HistoryDialog)
        observed["close_text"] = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).text()

        def inspect_loaded():
            observed["initial"] = dialog.results.toPlainText()
            dialog.search.setText("needle")

            def inspect_search():
                observed["filtered"] = dialog.results.toPlainText()
                dialog.accept()

            QTimer.singleShot(450, inspect_search)

        QTimer.singleShot(200, inspect_loaded)

    QTimer.singleShot(0, inspect_initial)
    view.open_history()
    qt_app.processEvents()

    assert observed["is_history_dialog"]
    assert observed["close_text"] == "Close"
    assert "Alpha transcript" in observed["initial"]
    assert "Needle transcript" in observed["initial"]
    assert "Needle transcript" in observed["filtered"]
    assert "Alpha transcript" not in observed["filtered"]


def test_completed_onboarding_opens_the_main_window(view, qt_app, monkeypatch):
    view.window.hide()
    view.state.config["onboarding_complete"] = False

    def complete_onboarding(dialog):
        dialog.app.state.config["onboarding_complete"] = True
        return QDialog.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", complete_onboarding)
    view.open_onboarding()
    qt_app.processEvents()

    assert view.window.isVisible()


def test_diagnostics_are_inside_settings_and_removed_from_main_surfaces(view, qt_app, monkeypatch):
    monkeypatch.setattr("autostart.is_enabled", lambda: False)
    dialog = SettingsDialog(view)
    dialog.show()
    dialog.tabs.setCurrentIndex(2)
    qt_app.processEvents()

    assert not dialog.diagnostics_field.isVisible()
    dialog.diagnostics_toggle.click()
    qt_app.processEvents()
    assert dialog.diagnostics_field.isVisible()
    assert dialog.autostart_enabled.isChecked() is False
    assert "Diagnostics" not in {button.text() for button in view.window.findChildren(QPushButton)}
    assert "Diagnostics" not in {action.text() for action in view.tray.contextMenu().actions()}
    dialog.close()


def test_settings_applies_autostart_checkbox(view, qt_app, monkeypatch):
    calls = []
    monkeypatch.setattr("autostart.is_enabled", lambda: False)
    monkeypatch.setattr("autostart.enable", lambda: calls.append("enable") or True)
    monkeypatch.setattr("autostart.disable", lambda: calls.append("disable") or True)
    monkeypatch.setattr("platform_integration.parse_hotkey", lambda value: value)
    dialog = SettingsDialog(view)
    dialog.autostart_enabled.setChecked(True)
    dialog.save()
    qt_app.processEvents()

    assert calls == ["enable"]


def test_settings_persists_start_in_tray_separately_from_autostart(view, qt_app, monkeypatch):
    monkeypatch.setattr("autostart.is_enabled", lambda: False)
    monkeypatch.setattr("platform_integration.parse_hotkey", lambda value: value)
    dialog = SettingsDialog(view)
    dialog.autostart_enabled.setChecked(False)
    dialog.start_in_tray.setChecked(True)
    dialog.save()
    qt_app.processEvents()

    assert view.state.config["start_in_tray"] is True
    assert should_show_main_window(view.state.config) is False
    assert should_show_main_window({"start_in_tray": False}) is True
    assert should_show_main_window({"start_in_tray": True}, force_show=True) is True


def test_onboarding_has_three_profile_gated_steps(view, qt_app):
    dialog = SettingsDialog(view, onboarding=True)
    dialog.show()
    qt_app.processEvents()

    assert dialog.pages.count() == 3
    assert dialog.pages.currentIndex() == 0
    assert dialog.backend_pages.currentIndex() == 0
    assert dialog.privacy_card.isChecked()
    assert dialog.progress.value() == 1
    assert "offline" in dialog.privacy_card.accessibleDescription().lower()

    dialog.speed_card.click()
    dialog.set_onboarding_step(1)
    qt_app.processEvents()

    assert dialog.profile.currentData() == "speed"
    assert dialog.progress.value() == 2
    assert dialog.backend_pages.currentIndex() == 1
    assert dialog.groq_key.isVisible()
    assert not dialog.model.isVisible()
    dialog.close()


def test_settings_hides_backend_controls_for_the_other_profile(view, qt_app):
    dialog = SettingsDialog(view)
    dialog.show()
    dialog.profile.setCurrentIndex(dialog.profile.findData("speed"))
    qt_app.processEvents()

    assert dialog.groq_key.isVisible()
    assert not dialog.model.isVisible()

    dialog.profile.setCurrentIndex(dialog.profile.findData("privacy"))
    qt_app.processEvents()
    assert not dialog.groq_key.isVisible()
    assert dialog.model.isVisible()
    dialog.close()


def test_settings_and_onboarding_fit_small_scaled_screen_with_scroll(view, qt_app, monkeypatch):
    monkeypatch.setattr("ui.available_dialog_size", lambda _widget: QSize(800, 600))
    settings = SettingsDialog(view)
    settings.show()
    qt_app.processEvents()

    assert settings.height() <= 552
    assert settings.maximumHeight() <= 576
    assert settings.settings_scroll.verticalScrollBar().maximum() > 0
    assert settings.findChild(QDialogButtonBox).isVisible()
    settings.close()

    onboarding = SettingsDialog(view, onboarding=True)
    onboarding.show()
    qt_app.processEvents()

    assert onboarding.height() <= 552
    assert onboarding.maximumHeight() <= 576
    assert onboarding.onboarding_scroll.verticalScrollBar().maximum() > 0
    onboarding.close()


def test_model_preparation_stays_in_dialog_with_progress_and_cancel(view, qt_app):
    calls = []

    class Jobs:
        current_job_id = None
        busy = False

        def prepare_model(self, model):
            calls.append(("prepare", model))
            self.current_job_id = "model-1"
            self.busy = True
            return True

        def cancel(self):
            calls.append(("cancel",))

    jobs = Jobs()
    view.state.jobs = jobs
    dialog = SettingsDialog(view)
    dialog.show()
    dialog.profile.setCurrentIndex(dialog.profile.findData("privacy"))
    dialog.prepare_model_button.click()
    qt_app.processEvents()

    assert calls[0] == ("prepare", dialog.model.currentData())
    assert dialog.isVisible()
    assert dialog.model_progress.isVisible()
    assert dialog.cancel_model_button.isVisible()
    dialog.cancel_model_button.click()
    assert calls[-1] == ("cancel",)

    jobs.busy = False
    view.state.tk_queue.put(("job", {"job_id": "model-1", "status": "idle", "kind": "model", "stage": "ready"}))
    view.drain_worker_events()
    dialog._poll_model_preparation()
    assert dialog.prepare_model_button.isEnabled()
    assert not dialog.model_progress.isVisible()
    assert dialog.model_status.text() == "Ready."
    assert dialog.model_status.isVisible()
    dialog.close()


def test_recording_pulse_respects_reduce_motion(view, qt_app):
    view.set_state(ViewState.RECORDING)
    qt_app.processEvents()
    assert view.recording_pulse.timer.isActive()
    assert view.hud.pulse.timer.isActive()

    view.state.config["hud"]["reduce_motion"] = True
    view.render_status()
    assert not view.recording_pulse.timer.isActive()
    assert not view.hud.pulse.timer.isActive()
