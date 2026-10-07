"""Regression tests for hold mode, silent recordings, line breaks and model reuse."""

import queue
import sys
import threading
import time
from types import SimpleNamespace

from test_jobs import FakeRecorder, job_events, make_controller
from test_platform_integration import fake_keyboard

import platform_integration
from inference_worker import inference_worker
from transcriber import _speech_text


def wait_idle(controller, timeout=1.0):
    deadline = time.monotonic() + timeout
    while controller.busy and time.monotonic() < deadline:
        time.sleep(0.01)


def test_hold_stops_when_modifier_is_released_first(monkeypatch):
    monkeypatch.setattr(platform_integration, "_pynput_keyboard", fake_keyboard)
    fired, released = [], []
    hotkey = platform_integration.GlobalHotkey("ctrl+space", lambda: fired.append(1), lambda: released.append(1))
    hotkey._keys = platform_integration.parse_hotkey("ctrl+space")
    hotkey._on_press("CTRL")
    hotkey._on_press("SPACE")
    hotkey._on_release("CTRL")
    hotkey._on_release("SPACE")
    assert fired == [1]
    assert released == [1]


def test_hold_release_never_starts_a_recording(tmp_path):
    state, worker, controller, _recording = make_controller(tmp_path)
    try:
        assert controller.stop_recording() is False
        assert not controller.busy
        assert worker.commands == []
        assert controller.start_recording() is True
        assert controller.start_recording() is False
        assert controller.stop_recording() is True
    finally:
        controller.shutdown()


def test_hotkey_hold_mode_uses_explicit_start_and_stop():
    import hotkey

    calls = []
    jobs = SimpleNamespace(
        start_recording=lambda: calls.append("start") or True,
        stop_recording=lambda: calls.append("stop") or True,
        toggle_recording=lambda: calls.append("toggle") or True,
    )
    listener = hotkey.HotkeyListener.__new__(hotkey.HotkeyListener)
    listener.state = SimpleNamespace(jobs=jobs)
    listener._start_recording()
    listener._stop_and_transcribe()
    assert calls == ["start", "stop"]


class SilentRecorder(FakeRecorder):
    voiced_seconds = 0.05


def test_silent_recording_is_not_sent_and_stays_retryable(tmp_path):
    state, worker, controller, recording = make_controller(tmp_path)
    controller._recorder_factory = lambda device, **kwargs: SilentRecorder(recording, device, **kwargs)
    try:
        assert controller.toggle_recording()
        assert controller.toggle_recording()
        assert worker.commands == []
        errors = [event for event in job_events(state) if event["status"] == "error"]
        assert errors[-1]["code"] == "empty_audio"
        assert errors[-1]["retry_available"] is True
        assert not controller.busy
    finally:
        controller.shutdown()


def test_dictation_with_line_breaks_is_typed_on_one_line(tmp_path):
    inserted = []
    state, worker, controller, _recording = make_controller(
        tmp_path,
        inserter=SimpleNamespace(insert=lambda text, cancelled=None: inserted.append(text) or "inserted"),
    )
    try:
        assert controller.toggle_recording()
        assert controller.toggle_recording()
        job_id = worker.commands[-1]["job_id"]
        worker.results.put({"type": "result", "job_id": job_id, "text": "Первый абзац.\n\nВторой\tабзац."})
        wait_idle(controller)
        assert inserted == ["Первый абзац. Второй абзац."]
        assert job_events(state)[-1]["text"] == "Первый абзац. Второй абзац."
    finally:
        controller.shutdown()


def test_worker_keeps_model_across_unrelated_setting_changes(monkeypatch):
    class FakeTranscriber:
        instances = 0

        def __init__(self, **kwargs):
            FakeTranscriber.instances += 1
            self.config = kwargs["config"]

        def transcribe(self, _path, language=None):
            return f"hud={self.config['hud']}"

    commands, results = queue.Queue(), queue.Queue()
    base = {"job_id": "one", "action": "dictation", "model": "small", "path": "a", "language": None}
    commands.put({**base, "config": {"hud": "on"}})
    commands.put({**base, "job_id": "two", "config": {"hud": "off"}})
    commands.put({**base, "job_id": "three", "model": "large", "config": {"hud": "off"}})
    commands.put({"action": "shutdown"})
    monkeypatch.setitem(sys.modules, "transcriber", SimpleNamespace(Transcriber=FakeTranscriber))

    inference_worker(commands, results, threading.Event())

    texts = []
    while not results.empty():
        message = results.get_nowait()
        if message["type"] == "result":
            texts.append(message["text"])
    assert texts == ["hud=on", "hud=off", "hud=off"]
    assert FakeTranscriber.instances == 2


def test_groq_segments_over_silence_are_dropped():
    transcription = SimpleNamespace(
        text=" Привет. Продолжение следует...",
        segments=[
            {"text": " Привет.", "no_speech_prob": 0.01, "avg_logprob": -0.2},
            {"text": " Продолжение следует...", "no_speech_prob": 0.9, "avg_logprob": -1.4},
        ],
    )
    assert _speech_text(transcription) == "Привет."
    assert _speech_text(SimpleNamespace(text="Текст", segments=None)) == "Текст"
    assert _speech_text(SimpleNamespace(text="Текст", segments=[{"text": "Текст"}])) == "Текст"
