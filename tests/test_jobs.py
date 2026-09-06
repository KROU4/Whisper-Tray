import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import jobs
import main
import release_smoke.tests.whispertray.__main__ as packaged_smoke
from core import DictationStateMachine
from inference_worker import inference_worker
from jobs import JobController


class FakeWorker:
    def __init__(self):
        self.commands = []
        self.results = queue.Queue()
        self.cancelled = False
        self.terminated = False

    def submit(self, command):
        self.commands.append(command)

    def get_result(self, timeout=0.2):
        try:
            return self.results.get(timeout=timeout)
        except queue.Empty:
            return None

    def request_cancel(self):
        self.cancelled = True

    def terminate(self):
        self.terminated = True

    def shutdown(self, _timeout=2.0):
        pass


class FakeRecorder:
    def __init__(self, path: Path, _device=None, on_warning=None, on_limit=None):
        self.path = path
        self.on_warning = on_warning
        self.on_limit = on_limit

    def start(self):
        self.path.write_bytes(b"RIFF-test")

    def stop(self):
        pass

    def cleanup(self):
        self.path.unlink(missing_ok=True)

    def shutdown(self):
        self.cleanup()

    def recording_snapshot(self):
        return {"elapsed": 1.0, "level": 0.5, "silent_seconds": 0.0}


def make_state():
    return SimpleNamespace(
        config={"profile": "privacy", "model": "small", "file_model": "large"},
        dictation_state=DictationStateMachine(),
        is_recording=threading.Event(),
        is_file_transcribing=threading.Event(),
        tk_queue=queue.Queue(),
        tray_app=None,
        on_transcript=None,
    )


def make_controller(tmp_path, *, inserter=None):
    state = make_state()
    worker = FakeWorker()
    recording = tmp_path / "recording.wav"
    controller = JobController(
        state,
        worker_client=worker,
        recorder_factory=lambda device, **kwargs: FakeRecorder(recording, device, **kwargs),
        inserter=inserter,
    )
    return state, worker, controller, recording


def job_events(state):
    events = []
    while not state.tk_queue.empty():
        command, payload = state.tk_queue.get_nowait()
        if command == "job":
            events.append(payload)
    return events


def test_briefcase_windows_launcher_enables_frozen_spawn(monkeypatch):
    called = []
    # Isolate the platform simulation: mutating os.name also changes pathlib
    # and pytest itself on POSIX runners.
    monkeypatch.setattr(main, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(main, "sys", SimpleNamespace(executable="WhisperTray.exe", frozen=False))
    monkeypatch.setattr(main.multiprocessing, "freeze_support", lambda: called.append(True))

    main._configure_multiprocessing()

    assert main.sys.frozen is True
    assert called == [True]


def test_briefcase_posix_launcher_handles_spawn_bootstrap_without_ui(monkeypatch):
    called = []
    monkeypatch.setattr(main.sys, "argv", ["pytest"])
    monkeypatch.setattr("multiprocessing.spawn.spawn_main", lambda **kwargs: called.append(kwargs))
    argv = [
        "/Applications/WhisperTray.app/Contents/MacOS/WhisperTray",
        "-c",
        "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=7, pipe_handle=11)",
        "--multiprocessing-fork",
    ]

    assert main._run_multiprocessing_bootstrap(argv) is True
    assert called == [{"tracker_fd": 7, "pipe_handle": 11}]
    assert main.sys.argv[1] == "--multiprocessing-fork"


def test_briefcase_posix_launcher_handles_resource_tracker_without_ui(monkeypatch):
    called = []
    monkeypatch.setattr("multiprocessing.resource_tracker.main", called.append)
    argv = [
        "/opt/WhisperTray/WhisperTray",
        "-c",
        "from multiprocessing.resource_tracker import main;main(9)",
    ]

    assert main._run_multiprocessing_bootstrap(argv) is True
    assert called == [9]


def test_briefcase_bootstrap_rejects_arbitrary_python_code(monkeypatch):
    monkeypatch.setattr("multiprocessing.spawn.spawn_main", lambda **_kwargs: None)
    assert main._run_multiprocessing_bootstrap(["WhisperTray", "-c", "print('unsafe')"]) is False


def test_packaged_smoke_configures_windows_frozen_spawn_before_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(packaged_smoke, "_run_multiprocessing_bootstrap", lambda: False)
    monkeypatch.setattr(packaged_smoke, "_configure_multiprocessing", lambda: calls.append("configure"))
    monkeypatch.setattr(packaged_smoke, "run_smoke", lambda: calls.append("smoke"))

    assert packaged_smoke.main() == 0
    assert calls == ["configure", "smoke"]


def test_packaged_smoke_child_bootstrap_skips_parent_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(packaged_smoke, "_run_multiprocessing_bootstrap", lambda: True)
    monkeypatch.setattr(packaged_smoke, "_configure_multiprocessing", lambda: calls.append("configure"))
    monkeypatch.setattr(packaged_smoke, "run_smoke", lambda: calls.append("smoke"))

    assert packaged_smoke.main() is None
    assert calls == []


def test_persistent_worker_rebinds_progress_to_each_job(monkeypatch):
    class FakeTranscriber:
        instances = 0

        def __init__(self, **kwargs):
            FakeTranscriber.instances += 1
            self.on_progress = kwargs["on_progress"]
            self.on_backend_switch = kwargs["on_backend_switch"]
            self.cancelled = kwargs["cancelled"]

        def transcribe(self, _path, language=None):
            self.on_progress("transcribing", 50)
            return f"result-{language}"

    commands = queue.Queue()
    results = queue.Queue()
    cancelled = threading.Event()
    config = {"profile": "privacy"}
    commands.put({"job_id": "one", "action": "dictation", "model": "small", "config": config, "path": "a", "language": "ru"})
    commands.put({"job_id": "two", "action": "dictation", "model": "small", "config": config, "path": "b", "language": "en"})
    commands.put({"action": "shutdown"})
    monkeypatch.setitem(sys.modules, "transcriber", SimpleNamespace(Transcriber=FakeTranscriber))

    inference_worker(commands, results, cancelled)

    messages = []
    while not results.empty():
        messages.append(results.get_nowait())
    progress_ids = [message["job_id"] for message in messages if message["type"] == "progress"]
    assert FakeTranscriber.instances == 1
    assert progress_ids == ["one", "two"]


def test_controller_rejects_file_while_recording_and_reports_live_snapshot(tmp_path):
    state, _worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    try:
        assert controller.toggle_recording() is True
        assert controller.busy is True
        assert controller.submit_file(source) is False
        assert controller.recording_snapshot() == {"elapsed": 1.0, "level": 0.5, "silent_seconds": 0.0}
    finally:
        controller.shutdown()


def test_recording_reservation_prevents_simultaneous_file_start(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    state = make_state()
    worker = FakeWorker()
    recording = tmp_path / "recording.wav"

    class BlockingRecorder(FakeRecorder):
        def start(self):
            entered.set()
            release.wait(1.0)
            super().start()

    controller = JobController(
        state,
        worker_client=worker,
        recorder_factory=lambda device, **kwargs: BlockingRecorder(recording, device, **kwargs),
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    recording_result = []
    file_result = []
    first = threading.Thread(target=lambda: recording_result.append(controller.toggle_recording()))
    second = threading.Thread(target=lambda: file_result.append(controller.submit_file(source)))
    try:
        first.start()
        assert entered.wait(0.5)
        second.start()
        release.set()
        first.join(1.0)
        second.join(1.0)
        assert recording_result == [True]
        assert file_result == [False]
    finally:
        release.set()
        controller.shutdown()


def test_cancelled_dictation_ignores_late_result_and_deletes_recording(tmp_path):
    inserted = []
    state, worker, controller, recording = make_controller(
        tmp_path,
        inserter=SimpleNamespace(insert=lambda text, cancelled=None: inserted.append(text) or "inserted"),
    )
    assert controller.toggle_recording()
    assert controller.toggle_recording()
    job_id = worker.commands[-1]["job_id"]
    assert controller.cancel()
    worker.results.put({"type": "result", "job_id": job_id, "text": "late text"})
    deadline = time.monotonic() + 1.0
    while controller.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert inserted == []
        assert not recording.exists()
        assert any(event["status"] == "cancelled" for event in job_events(state))
    finally:
        controller.shutdown()


def test_cancel_during_insertion_stops_typing_and_keeps_result_recoverable(tmp_path):
    started = threading.Event()

    class CancellableInserter:
        def insert(self, _text, cancelled=None):
            started.set()
            deadline = time.monotonic() + 1.0
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(0.005)
            return "cancelled"

    state, worker, controller, recording = make_controller(tmp_path, inserter=CancellableInserter())
    assert controller.toggle_recording()
    assert controller.toggle_recording()
    job_id = worker.commands[-1]["job_id"]
    worker.results.put({"type": "result", "job_id": job_id, "text": "recoverable text"})
    assert started.wait(0.5)
    assert controller.cancel()
    deadline = time.monotonic() + 1.0
    while controller.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        cancelled = [event for event in job_events(state) if event["status"] == "cancelled"][-1]
        assert cancelled["text"] == "recoverable text"
        assert not recording.exists()
    finally:
        controller.shutdown()


def test_inserter_type_error_after_partial_input_is_never_retried(monkeypatch, tmp_path):
    calls = []
    copied = []

    class PartiallyFailingInserter:
        def insert(self, text, cancelled=None):
            calls.append(text)
            raise TypeError("failed after a partial input")

    monkeypatch.setattr(
        "jobs.TextInserter._copy_fallback",
        lambda text: copied.append(text) or "clipboard",
    )
    state, worker, controller, recording = make_controller(tmp_path, inserter=PartiallyFailingInserter())
    try:
        assert controller.toggle_recording()
        assert controller.toggle_recording()
        job_id = worker.commands[-1]["job_id"]
        controller._handle_worker_result({"type": "result", "job_id": job_id, "text": "complete text"})
        errors = [event for event in job_events(state) if event["status"] == "error"]
        assert calls == ["complete text"]
        assert copied == ["complete text"]
        assert errors[-1]["code"] == "clipboard_fallback"
        assert errors[-1]["text"] == "complete text"
        assert not recording.exists()
    finally:
        controller.shutdown()


def test_failed_dictation_is_retained_and_can_be_retried(tmp_path):
    _state, worker, controller, recording = make_controller(tmp_path)
    try:
        assert controller.toggle_recording()
        assert controller.toggle_recording()
        first_id = worker.commands[-1]["job_id"]
        controller._handle_worker_result(
            {"type": "error", "job_id": first_id, "code": "network", "message": "offline", "retryable": True}
        )
        assert recording.exists()
        assert controller.retry() is True
        assert len(worker.commands) == 2
        assert worker.commands[-1]["path"] == str(recording)
        assert worker.commands[-1]["job_id"] != first_id
    finally:
        controller.shutdown()


def test_file_save_failure_still_publishes_transcribed_text(monkeypatch, tmp_path):
    state, worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(controller, "_save_file_result", lambda *_args: None)
    try:
        assert controller.submit_file(source)
        job_id = worker.commands[-1]["job_id"]
        controller._handle_worker_result({"type": "result", "job_id": job_id, "text": "Recovered text."})
        event = [event for event in job_events(state) if event["status"] == "error"][-1]
        assert event["code"] == "save_failed"
        assert event["text"] == "Recovered text."
    finally:
        controller.shutdown()


def test_stale_result_cannot_replace_newer_job(tmp_path):
    inserted = []
    state, worker, controller, _recording = make_controller(
        tmp_path,
        inserter=SimpleNamespace(insert=lambda text, cancelled=None: inserted.append(text) or "inserted"),
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    try:
        assert controller.submit_file(source)
        current_id = controller.current_job_id
        controller._handle_worker_result({"type": "result", "job_id": "obsolete", "text": "stale"})
        assert controller.current_job_id == current_id
        assert inserted == []
        assert controller.busy is True
    finally:
        controller.shutdown()


def test_stale_event_cannot_override_compatibility_state(tmp_path):
    state, _worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    try:
        assert controller.submit_file(source)
        controller._publish("recording", "dictation", "obsolete")
        assert state.is_file_transcribing.is_set()
        assert not state.is_recording.is_set()
    finally:
        controller.shutdown()


def test_worker_death_finishes_active_job_with_error(tmp_path):
    state, worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    try:
        assert controller.submit_file(source)
        worker.results.put({"type": "worker_exit"})
        deadline = time.monotonic() + 1.0
        while controller.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        errors = [event for event in job_events(state) if event["status"] == "error"]
        assert controller.busy is False
        assert worker.terminated is True
        assert errors[-1]["code"] == "worker_stopped"
    finally:
        controller.shutdown()


def test_uncooperative_worker_is_terminated_after_cancel_grace(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "CANCEL_GRACE_SECONDS", 0.03)
    _state, worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    try:
        assert controller.submit_file(source)
        assert controller.cancel()
        deadline = time.monotonic() + 1.0
        while controller.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.cancelled is True
        assert worker.terminated is True
        assert controller.busy is False
    finally:
        controller.shutdown()


def test_terminal_job_id_remains_available_for_queued_ui_events(tmp_path):
    _state, worker, controller, _recording = make_controller(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    monkeypatch_output = tmp_path / "saved.txt"
    controller._save_file_result = lambda *_args: monkeypatch_output
    try:
        assert controller.submit_file(source)
        job_id = worker.commands[-1]["job_id"]
        controller._handle_worker_result({"type": "result", "job_id": job_id, "text": "done"})
        assert controller.busy is False
        assert controller.current_job_id == job_id
    finally:
        controller.shutdown()


def test_retained_recording_expiry_removes_retry_and_notifies_ui(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "RETRY_TTL_SECONDS", 0.03)
    state, worker, controller, recording = make_controller(tmp_path)
    try:
        assert controller.toggle_recording()
        assert controller.toggle_recording()
        job_id = worker.commands[-1]["job_id"]
        controller._handle_worker_result(
            {"type": "error", "job_id": job_id, "code": "network", "message": "offline"}
        )
        deadline = time.monotonic() + 1.0
        expired = None
        while time.monotonic() < deadline:
            expired = next(
                (event for event in job_events(state) if event.get("code") == "retry_expired"), None
            )
            if expired is not None:
                break
            time.sleep(0.01)
        assert expired is not None
        assert expired["retry_available"] is False
        assert not recording.exists()
        assert controller.retry() is False
    finally:
        controller.shutdown()
