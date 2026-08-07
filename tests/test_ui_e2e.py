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

from PySide6.QtWidgets import QApplication  # noqa: E402

from config_store import DEFAULT_CONFIG  # noqa: E402
from ui import SettingsDialog, ViewState, WhisperTrayUi  # noqa: E402


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
