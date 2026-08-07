import queue
import threading
from types import SimpleNamespace

import numpy as np

from core import DictationStateMachine, DictationStatus
from file_transcriber import FileTranscriptionWorker
from hotkey import HotkeyListener
from recorder import AudioRecorder
from transcriber import GROQ_BACKEND, GroqTranscriptionError, Transcriber


class FakeWav:
    def __init__(self):
        self.closed = False

    def writeframes(self, _data):
        assert not self.closed

    def close(self):
        self.closed = True


def test_recorder_stop_does_not_hold_callback_lock():
    recorder = AudioRecorder(max_duration_seconds=1)
    recorder._wav = FakeWav()

    class FakeStream:
        def stop(self):
            callback = threading.Thread(
                target=recorder._callback,
                args=(np.zeros((1, 1), dtype=np.float32), 1, None, None),
            )
            callback.start()
            callback.join(0.5)
            assert not callback.is_alive()

        def close(self):
            pass

    recorder._stream = FakeStream()
    recorder.stop()
    assert recorder._wav is None


def test_dictation_is_rejected_while_file_job_is_active():
    tray = SimpleNamespace(notify=lambda *_: None, set_recording=lambda *_: None)
    state = SimpleNamespace(
        config={},
        dictation_state=DictationStateMachine(),
        operation_lock=threading.RLock(),
        is_recording=threading.Event(),
        is_file_transcribing=threading.Event(),
        tk_queue=queue.Queue(),
        tray_app=tray,
    )
    state.is_file_transcribing.set()
    listener = HotkeyListener(state)
    listener._start_recording()
    assert state.dictation_state.status is DictationStatus.IDLE


def test_file_job_is_rejected_while_dictation_is_processing():
    state = SimpleNamespace(
        config={},
        dictation_state=DictationStateMachine(),
        operation_lock=threading.RLock(),
        is_recording=threading.Event(),
        is_file_transcribing=threading.Event(),
        tk_queue=queue.Queue(),
        tray_app=None,
    )
    state.dictation_state.transition({DictationStatus.IDLE}, DictationStatus.PROCESSING)
    assert FileTranscriptionWorker(state).start("unused.wav") is False


def test_explicit_cloud_fallback_is_visible(monkeypatch):
    messages = []
    transcriber = Transcriber(
        config={"profile": "speed", "transcription_backend": "groq", "allow_local_fallback": True},
        on_backend_switch=messages.append,
    )
    monkeypatch.setattr(transcriber, "_backend", lambda: GROQ_BACKEND)
    monkeypatch.setattr(
        transcriber,
        "_transcribe_groq_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(GroqTranscriptionError("network", "offline")),
    )
    monkeypatch.setattr(transcriber, "_can_fallback_locally", lambda: True)
    monkeypatch.setattr(transcriber, "_transcribe_local_audio", lambda *_args, **_kwargs: "Local result.")

    assert transcriber.transcribe(np.zeros(16, dtype=np.float32)) == "Local result."
    assert messages and "switching" in messages[0]


def test_paste_uses_cross_platform_inserter(monkeypatch):
    state = SimpleNamespace(
        config={}, dictation_state=DictationStateMachine(), operation_lock=threading.RLock(),
        is_recording=threading.Event(), is_file_transcribing=threading.Event(), tk_queue=queue.Queue(), tray_app=None,
    )
    monkeypatch.setattr("hotkey.TextInserter.insert", lambda _self, text: "clipboard")
    assert HotkeyListener(state)._paste_text("hello") == "clipboard"
