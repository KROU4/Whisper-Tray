"""Persistent spawned-process entry point for transcription work."""

from __future__ import annotations

import json

from core import BackendError


def _signature(model: str, config: dict) -> str:
    # Transcriber reads the rest of the config live; only the model needs a reload.
    return json.dumps({"model": model}, sort_keys=True)


def _configure_worker_logging() -> None:
    """A spawned process starts without the UI's handlers; give it its own file."""
    import multiprocessing

    if multiprocessing.current_process().name == "MainProcess":
        return  # In-process callers (tests, tools) keep their own logging.
    try:
        from config_store import app_data_dir
        from logging_setup import configure_logging

        configure_logging(app_data_dir(), filename="inference.log")
    except Exception:
        pass  # Logging must never prevent transcription.


def inference_worker(command_queue, result_queue, cancel_event) -> None:
    """Run one transcription at a time while retaining the loaded model."""
    _configure_worker_logging()
    transcriber = None
    transcriber_signature = None
    while True:
        try:
            command = command_queue.get()
        except (EOFError, OSError):
            return
        if command is None or command.get("action") == "shutdown":
            return

        job_id = command["job_id"]
        model = command["model"]
        config = command["config"]
        signature = _signature(model, config)

        try:
            def on_progress(stage, progress=None):
                if not cancel_event.is_set():
                    result_queue.put(
                        {"type": "progress", "job_id": job_id, "stage": stage, "progress": progress}
                    )

            def on_backend_switch(message):
                if not cancel_event.is_set():
                    result_queue.put(
                        {
                            "type": "progress",
                            "job_id": job_id,
                            "stage": "local_fallback",
                            "progress": None,
                            "message": message,
                        }
                    )

            if transcriber is None or signature != transcriber_signature:
                from transcriber import Transcriber

                transcriber = Transcriber(
                    model_size=model,
                    config=config,
                    on_backend_switch=on_backend_switch,
                    on_progress=on_progress,
                    cancelled=cancel_event.is_set,
                )
                transcriber_signature = signature
            else:
                transcriber.config = config
                transcriber.on_backend_switch = on_backend_switch
                transcriber.on_progress = on_progress
                transcriber.cancelled = cancel_event.is_set

            if cancel_event.is_set():
                raise BackendError("cancelled", "Transcription was cancelled")

            action = command["action"]
            if action == "prepare":
                prepare = getattr(transcriber, "prepare", None)
                if prepare is not None:
                    prepare()
                else:
                    transcriber._ensure_model()
                text = ""
            elif action == "file":
                text = transcriber.transcribe_file(command["path"], language=command.get("language"))
            else:
                text = transcriber.transcribe(command["path"], language=command.get("language"))
                polish = getattr(transcriber, "polish", None)
                if polish is not None:
                    text = polish(text, language=command.get("language"))

            if cancel_event.is_set():
                raise BackendError("cancelled", "Transcription was cancelled")
            result_queue.put({"type": "result", "job_id": job_id, "text": text})
        except BackendError as exc:
            result_queue.put(
                {
                    "type": "error",
                    "job_id": job_id,
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                }
            )
        except OSError:
            result_queue.put(
                {
                    "type": "error",
                    "job_id": job_id,
                    "code": "file_unavailable",
                    "message": "The audio file is unavailable",
                    "retryable": False,
                }
            )
        except Exception:
            result_queue.put(
                {
                    "type": "error",
                    "job_id": job_id,
                    "code": "transcription_failed",
                    "message": "Could not transcribe the audio",
                    "retryable": False,
                }
            )
