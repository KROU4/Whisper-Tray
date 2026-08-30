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

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

from config_store import DEFAULT_CONFIG  # noqa: E402
from ui import (  # noqa: E402
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


def test_recording_pulse_respects_reduce_motion(view, qt_app):
    view.set_state(ViewState.RECORDING)
    qt_app.processEvents()
    assert view.recording_pulse.timer.isActive()
    assert view.hud.pulse.timer.isActive()

    view.state.config["hud"]["reduce_motion"] = True
    view.render_status()
    assert not view.recording_pulse.timer.isActive()
    assert not view.hud.pulse.timer.isActive()
