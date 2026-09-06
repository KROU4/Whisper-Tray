"""Unified recording, model preparation, and file-transcription controller."""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from config_store import app_data_dir
from core import DictationStatus, Profile
from platform_integration import TextInserter

logger = logging.getLogger(__name__)
RETRY_TTL_SECONDS = 5 * 60
CANCEL_GRACE_SECONDS = 2.0
CLOUD_JOB_BUDGET_SECONDS = 3 * 60


class InferenceWorkerClient:
    """Own a reusable spawned worker and its cooperative cancellation flag."""

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._commands = None
        self._results = None
        self._cancel_event = None
        self._process = None
        self._lock = threading.RLock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            from inference_worker import inference_worker

            self._commands = self._context.Queue()
            self._results = self._context.Queue()
            self._cancel_event = self._context.Event()
            self._process = self._context.Process(
                target=inference_worker,
                args=(self._commands, self._results, self._cancel_event),
                name="WhisperTrayInference",
                daemon=False,
            )
            self._process.start()

    def submit(self, command: dict) -> None:
        self._ensure_started()
        self._cancel_event.clear()
        self._commands.put(command)

    def get_result(self, timeout: float = 0.2) -> dict | None:
        results = self._results
        if results is None:
            time.sleep(min(timeout, 0.05))
            return None
        try:
            return results.get(timeout=timeout)
        except queue.Empty:
            process = self._process
            if process is not None and not process.is_alive():
                return {"type": "worker_exit"}
            return None
        except (EOFError, OSError):
            return {"type": "worker_exit"}

    def request_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def terminate(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.is_alive():
                process.terminate()
                process.join(CANCEL_GRACE_SECONDS)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(1.0)
            self._process = None
            self._commands = None
            self._results = None
            self._cancel_event = None

    def shutdown(self, timeout: float = CANCEL_GRACE_SECONDS) -> None:
        with self._lock:
            process = self._process
            commands = self._commands
        if process is None:
            return
        if process.is_alive() and commands is not None:
            commands.put({"action": "shutdown"})
            process.join(timeout)
        if process.is_alive():
            self.terminate()
        else:
            with self._lock:
                self._process = None


class JobController:
    """Serialise all runtime jobs and gate every event by a unique job id."""

    def __init__(self, state, *, worker_client=None, recorder_factory=None, inserter=None) -> None:
        self.state = state
        self._worker = worker_client or InferenceWorkerClient()
        self._recorder_factory = recorder_factory
        self._inserter = inserter or TextInserter()
        self._recorder = None
        self._recorder_device = object()
        self._lock = threading.RLock()
        self._completion = threading.Condition(self._lock)
        self._completed_job_ids = deque(maxlen=128)
        self._active: dict | None = None
        self._current_job_id: str | None = None
        self._cancel_in_progress = False
        self._shutdown = False
        self._retained_recording: tuple[Path, float] | None = None
        self._retained_job_id: str | None = None
        self._retry_timer: threading.Timer | None = None
        self._budget_timer: threading.Timer | None = None
        self._result_thread = threading.Thread(
            target=self._result_loop, daemon=True, name="WhisperTrayJobResults"
        )
        self._result_thread.start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None or self._cancel_in_progress

    @property
    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current_job_id

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    def _emit(self, payload: dict) -> None:
        event = {
            "job_id": payload.get("job_id"),
            "status": payload["status"],
            "kind": payload["kind"],
        }
        event.update({key: value for key, value in payload.items() if value is not None})
        events = getattr(self.state, "tk_queue", None)
        if events is not None:
            events.put(("job", event))

    def _set_compatibility_state(self, status: str, kind: str) -> None:
        recording = getattr(self.state, "is_recording", None)
        file_running = getattr(self.state, "is_file_transcribing", None)
        if recording is not None:
            (recording.set if status == "recording" else recording.clear)()
        if file_running is not None:
            (file_running.set if kind == "file" and status in {"preparing", "processing"} else file_running.clear)()
        machine = getattr(self.state, "dictation_state", None)
        if machine is None or kind != "dictation":
            return
        if status == "recording":
            machine.reset()
            machine.transition({DictationStatus.IDLE}, DictationStatus.RECORDING)
        elif status in {"preparing", "processing"}:
            if not machine.transition({DictationStatus.RECORDING}, DictationStatus.PROCESSING):
                machine.reset()
                machine.transition({DictationStatus.IDLE}, DictationStatus.RECORDING)
                machine.transition({DictationStatus.RECORDING}, DictationStatus.PROCESSING)
        elif status == "inserted":
            machine.transition({DictationStatus.PROCESSING}, DictationStatus.INSERTED)
        elif status == "error":
            machine.transition(
                {DictationStatus.RECORDING, DictationStatus.PROCESSING}, DictationStatus.ERROR
            )
        elif status in {"idle", "cancelled"}:
            machine.reset()

    def _publish(self, status: str, kind: str, job_id: str | None, **fields) -> None:
        with self._lock:
            if job_id is not None and self._current_job_id != job_id:
                return
            self._set_compatibility_state(status, kind)
            self._emit({"job_id": job_id, "status": status, "kind": kind, **fields})

    def _get_recorder(self):
        device = self.state.config.get("device_index")
        if self._recorder is not None and self._recorder_device == device:
            return self._recorder
        if self._recorder is not None:
            self._recorder.cleanup()
        if self._recorder_factory is None:
            from recorder import AudioRecorder

            factory = AudioRecorder
        else:
            factory = self._recorder_factory
        self._recorder = factory(
            device,
            on_warning=lambda message: self._recording_warning(message),
            on_limit=lambda: threading.Thread(target=self._stop_recording, daemon=True).start(),
        )
        self._recorder_device = device
        return self._recorder

    def _recording_warning(self, message: str) -> None:
        with self._lock:
            active = self._active
            if active is None or active["kind"] != "dictation" or active["status"] != "recording":
                return
            job_id = active["job_id"]
        self._publish("recording", "dictation", job_id, stage="limit_warning", message=message)

    def toggle_recording(self) -> bool:
        with self._lock:
            if self._shutdown or self._cancel_in_progress:
                return False
            if self._active is not None:
                if self._active["kind"] == "dictation" and self._active["status"] == "recording":
                    stop = True
                else:
                    return False
            else:
                stop = False
        if stop:
            return self._stop_recording()
        return self._start_recording()

    def _start_recording(self) -> bool:
        job_id = self._new_id()
        with self._lock:
            if self._shutdown or self._active is not None or self._cancel_in_progress:
                return False
            self._active = {"job_id": job_id, "kind": "dictation", "status": "starting"}
            self._current_job_id = job_id
            self._discard_retained_recording()
            try:
                recorder = self._get_recorder()
                recorder.start()
            except Exception:
                logger.exception("Could not start recording")
                if self._recorder is not None:
                    self._recorder.cleanup()
                self._terminal_error(
                    job_id, "dictation", "microphone_unavailable", "Microphone is unavailable"
                )
                return False
            self._active["status"] = "recording"
        self._publish("recording", "dictation", job_id, stage="capturing")
        tray = getattr(self.state, "tray_app", None)
        if tray is not None:
            tray.set_recording(True)
        return True

    def _stop_recording(self) -> bool:
        with self._lock:
            active = self._active
            if active is None or active["kind"] != "dictation" or active["status"] != "recording":
                return False
            active["status"] = "processing"
            job_id = active["job_id"]
        recorder = self._get_recorder()
        try:
            recorder.stop()
            path = recorder.path
            if path is None or not path.exists():
                raise OSError("recording was not created")
        except Exception:
            recorder.cleanup()
            self._terminal_error(job_id, "dictation", "recording_failed", "Could not finish the recording")
            return False
        with self._lock:
            if self._active is None or self._active["job_id"] != job_id:
                return False
            self._active["path"] = path
        tray = getattr(self.state, "tray_app", None)
        if tray is not None:
            tray.set_recording(False)
        self._publish("processing", "dictation", job_id, stage="transcribing")
        self._submit_worker(job_id, "dictation", "dictation", path)
        return True

    def submit_file(self, path, use_local: bool = False) -> bool:
        source = Path(path)
        if not source.is_file():
            return False
        with self._lock:
            if self._shutdown or self._active is not None or self._cancel_in_progress:
                return False
            job_id = self._new_id()
            self._active = {
                "job_id": job_id, "kind": "file", "status": "processing", "path": source,
                "use_local": use_local,
            }
            self._current_job_id = job_id
        self._publish("processing", "file", job_id, stage="validating")
        self._submit_worker(job_id, "file", "file", source, use_local=use_local)
        return True

    def prepare_model(self, model: str) -> bool:
        with self._lock:
            if self._shutdown or self._active is not None or self._cancel_in_progress:
                return False
            job_id = self._new_id()
            self._active = {"job_id": job_id, "kind": "model", "status": "preparing", "model": model}
            self._current_job_id = job_id
        self._publish("preparing", "model", job_id, stage="loading")
        self._submit_worker(job_id, "model", "prepare", None, model=model, use_local=True)
        return True

    def _submit_worker(self, job_id, kind, action, path, *, model=None, use_local=False) -> None:
        config = dict(self.state.config)
        if use_local:
            config["profile"] = Profile.PRIVACY.value
            config["transcription_backend"] = "local"
            config["allow_local_fallback"] = False
        selected_model = model or (config.get("file_model", "large") if kind == "file" else config.get("model", "small"))
        command = {
            "job_id": job_id,
            "action": action,
            "path": str(path) if path is not None else None,
            "language": config.get("language"),
            "model": selected_model,
            "config": config,
        }
        try:
            self._worker.submit(command)
        except Exception:
            logger.exception("Could not start inference worker")
            self._terminal_error(job_id, kind, "worker_start_failed", "Could not start transcription")
            return
        if config.get("profile") == Profile.SPEED.value and not use_local:
            timer = threading.Timer(CLOUD_JOB_BUDGET_SECONDS, self._budget_expired, args=(job_id,))
            timer.daemon = True
            with self._lock:
                self._budget_timer = timer
            timer.start()

    def _budget_expired(self, job_id: str) -> None:
        with self._lock:
            if self._active is None or self._active["job_id"] != job_id:
                return
        self.cancel(code="timeout", message="Cloud transcription timed out", as_error=True)

    def recording_snapshot(self) -> dict[str, float]:
        recorder = self._recorder
        if recorder is None:
            return {"elapsed": 0.0, "level": 0.0, "silent_seconds": 0.0}
        return recorder.recording_snapshot()

    def retry(self) -> bool:
        with self._lock:
            retained = self._retained_recording
            if self._shutdown or self._active is not None or self._cancel_in_progress or retained is None:
                return False
            path, expires = retained
            if expires <= time.monotonic() or not path.exists():
                expired = True
            else:
                expired = False
                job_id = self._new_id()
                self._active = {
                    "job_id": job_id, "kind": "dictation", "status": "processing", "path": path,
                }
                self._current_job_id = job_id
                self._retained_recording = None
                self._cancel_retry_timer_locked()
        if expired:
            self._discard_retained_recording()
            return False
        self._publish("processing", "dictation", job_id, stage="retrying")
        self._submit_worker(job_id, "dictation", "dictation", path)
        return True

    def cancel(
        self, *, code: str = "cancelled", message: str = "Operation cancelled", as_error: bool = False
    ) -> bool:
        with self._lock:
            active = self._active
            if active is None:
                self._discard_retained_recording()
                return False
            job_id, kind = active["job_id"], active["kind"]
            path = active.get("path") if kind == "dictation" else None
            recording = active["status"] in {"starting", "recording"}
            text = active.get("text")
            self._active = None
            self._cancel_budget_timer_locked()
            self._cancel_in_progress = not recording
        if recording:
            self._get_recorder().cleanup()
            self._publish("cancelled", kind, job_id, code=code, message=message, text=text)
            return True
        self._worker.request_cancel()
        terminal_status = "error" if as_error else "cancelled"
        self._publish(
            terminal_status, kind, job_id, code=code, message=message,
            retry_available=as_error and kind == "dictation" and path is not None, text=text,
        )
        threading.Thread(
            target=self._finish_cancel, args=(job_id, Path(path) if path else None, as_error),
            daemon=True, name="WhisperTrayCancel",
        ).start()
        return True

    def _finish_cancel(self, job_id: str, recording_path: Path | None, retain_recording: bool) -> None:
        deadline = time.monotonic() + CANCEL_GRACE_SECONDS
        with self._completion:
            while job_id not in self._completed_job_ids and time.monotonic() < deadline:
                self._completion.wait(deadline - time.monotonic())
            completed = job_id in self._completed_job_ids
        if not completed:
            self._worker.terminate()
        if recording_path is not None:
            if retain_recording and recording_path.exists():
                self._retain_recording(recording_path, job_id)
            else:
                self._cleanup_recording(recording_path)
        with self._lock:
            self._cancel_in_progress = False

    def _result_loop(self) -> None:
        while True:
            with self._lock:
                if self._shutdown:
                    return
            try:
                result = self._worker.get_result(0.2)
            except Exception:
                logger.exception("Inference worker result channel failed")
                time.sleep(0.2)
                continue
            if result is None:
                continue
            if result.get("type") == "worker_exit":
                with self._lock:
                    active = self._active
                if active is not None:
                    self._terminal_error(
                        active["job_id"], active["kind"], "worker_stopped",
                        "The transcription process stopped unexpectedly",
                    )
                self._worker.terminate()
                continue
            job_id = result.get("job_id")
            if result.get("type") in {"result", "error"}:
                with self._completion:
                    self._completed_job_ids.append(job_id)
                    self._completion.notify_all()
            self._handle_worker_result(result)

    def _handle_worker_result(self, result: dict) -> None:
        job_id = result.get("job_id")
        with self._lock:
            active = self._active
            if self._shutdown or active is None or active["job_id"] != job_id:
                return
            kind = active["kind"]
        if result.get("type") == "progress":
            status = "preparing" if kind == "model" else "processing"
            if result.get("stage") == "local_fallback":
                with self._lock:
                    self._cancel_budget_timer_locked()
            self._publish(
                status, kind, job_id, stage=result.get("stage"), progress=result.get("progress"),
                message=result.get("message"),
            )
            return
        if result.get("type") == "error":
            self._terminal_error(
                job_id, kind, result.get("code", "transcription_failed"),
                result.get("message", "Could not transcribe the audio"),
                retryable=bool(result.get("retryable")),
            )
            return
        self._handle_success(job_id, kind, result.get("text", ""))

    def _handle_success(self, job_id: str, kind: str, text: str) -> None:
        if kind == "model":
            if not self._finish_active(job_id):
                return
            self._publish("idle", kind, job_id, stage="ready")
            return
        if not text:
            self._terminal_error(job_id, kind, "empty_audio", "No speech was detected")
            return
        with self._lock:
            active = self._active
            if active is None or active["job_id"] != job_id or self._shutdown:
                return
            source = active.get("path")
        if kind == "file":
            output = self._save_file_result(Path(source), text)
            if not self._finish_active(job_id):
                return
            callback = getattr(self.state, "on_transcript", None)
            if callable(callback):
                callback(text)
            if output is None:
                self._publish(
                    "error", kind, job_id, code="save_failed", message="The transcript could not be saved",
                    text=text, retry_available=False,
                )
            else:
                self._publish("idle", kind, job_id, text=text, output_path=str(output))
            return

        with self._lock:
            active = self._active
            if active is None or active["job_id"] != job_id or self._shutdown:
                return
            active["text"] = text

        def cancelled() -> bool:
            with self._lock:
                return self._shutdown or self._active is None or self._active["job_id"] != job_id

        try:
            insertion = self._inserter.insert(text, cancelled=cancelled)
        except Exception as exc:
            # The adapter may have inserted a prefix before failing. Never call
            # it again: preserve the complete result through the clipboard and
            # let the user replace any partial text explicitly.
            logger.warning("Text insertion adapter failed (%s)", type(exc).__name__)
            insertion = TextInserter._copy_fallback(text)
        with self._lock:
            active = self._active
            if active is None or active["job_id"] != job_id or self._shutdown or insertion == "cancelled":
                return
            self._active = None
            self._cancel_budget_timer_locked()
        callback = getattr(self.state, "on_transcript", None)
        if callable(callback):
            callback(text)
        self._cleanup_recording(Path(source))
        if insertion == "inserted":
            self._publish("inserted", kind, job_id, text=text)
        else:
            message = (
                "The complete text was copied. Replace any partial text, then press Ctrl+V."
                if insertion == "clipboard"
                else "Automatic insertion and clipboard fallback both failed."
            )
            self._publish(
                "error", kind, job_id, code="clipboard_fallback", message=message,
                text=text, retry_available=False,
            )

    def _terminal_error(self, job_id, kind, code, message, *, retryable=False) -> None:
        with self._lock:
            active = self._active
            if active is None or active["job_id"] != job_id:
                return
            path = active.get("path")
            self._active = None
            self._cancel_budget_timer_locked()
        retain = kind == "dictation" and path is not None and Path(path).exists() and code != "cancelled"
        if retain:
            self._retain_recording(Path(path), job_id)
        self._publish(
            "error", kind, job_id, code=code, message=message,
            retry_available=retain or retryable,
        )

    def _finish_active(self, job_id: str) -> bool:
        with self._lock:
            if self._active is None or self._active["job_id"] != job_id:
                return False
            self._active = None
            self._cancel_budget_timer_locked()
            return True

    def _retain_recording(self, path: Path, job_id: str | None = None) -> None:
        self._discard_retained_recording()
        expires = time.monotonic() + RETRY_TTL_SECONDS
        timer = threading.Timer(RETRY_TTL_SECONDS, self._expire_retained, args=(path, expires))
        timer.daemon = True
        with self._lock:
            self._retained_recording = (path, expires)
            self._retained_job_id = job_id
            self._retry_timer = timer
        timer.start()

    def _expire_retained(self, path: Path, expires: float) -> None:
        with self._lock:
            if self._retained_recording != (path, expires):
                return
            self._retained_recording = None
            job_id = self._retained_job_id
            self._retained_job_id = None
            self._retry_timer = None
        self._cleanup_recording(path)
        self._publish(
            "error", "dictation", job_id, code="retry_expired",
            message="The saved recording expired", retry_available=False,
        )

    def _discard_retained_recording(self) -> None:
        with self._lock:
            retained = self._retained_recording
            self._retained_recording = None
            self._retained_job_id = None
            self._cancel_retry_timer_locked()
        if retained is not None:
            self._cleanup_recording(retained[0])

    def _cleanup_recording(self, path: Path) -> None:
        recorder = self._recorder
        if recorder is not None and recorder.path == path:
            recorder.cleanup()
        else:
            self._unlink(path)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove temporary recording")

    @staticmethod
    def _save_file_result(source: Path, text: str) -> Path | None:
        temporary = None
        try:
            out_dir = app_data_dir() / "Output"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            destination = out_dir / f"{source.stem}_{stamp}_{uuid.uuid4().hex[:6]}.txt"
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".whispertray-", suffix=".tmp",
                dir=out_dir, delete=False,
            ) as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
                temporary = Path(output.name)
            os.replace(temporary, destination)
            return destination
        except OSError:
            logger.exception("Could not save transcript")
            if temporary is not None:
                JobController._unlink(temporary)
            return None

    def _cancel_retry_timer_locked(self) -> None:
        if self._retry_timer is not None:
            self._retry_timer.cancel()
            self._retry_timer = None

    def _cancel_budget_timer_locked(self) -> None:
        if self._budget_timer is not None:
            self._budget_timer.cancel()
            self._budget_timer = None

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            active = self._active
        if active is not None:
            self.cancel()
        deadline = time.monotonic() + CANCEL_GRACE_SECONDS
        while self._cancel_in_progress and time.monotonic() < deadline:
            time.sleep(0.02)
        if self._cancel_in_progress:
            self._worker.terminate()
            with self._lock:
                self._cancel_in_progress = False
        with self._lock:
            self._shutdown = True
            self._cancel_budget_timer_locked()
        self._worker.shutdown(CANCEL_GRACE_SECONDS)
        self._discard_retained_recording()
        if self._recorder is not None:
            self._recorder.shutdown()
