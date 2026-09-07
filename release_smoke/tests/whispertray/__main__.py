"""Run bounded runtime checks inside a Briefcase-built application."""

from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

from main import _configure_multiprocessing, _run_multiprocessing_bootstrap


def _wait_for_error(client, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get_result(min(0.2, deadline - time.monotonic()))
        if result is None or result.get("job_id") != job_id:
            continue
        if result.get("type") == "error":
            return result
        if result.get("type") == "result":
            raise RuntimeError("Malformed smoke input was unexpectedly transcribed")
    raise TimeoutError("Inference worker did not return a bounded response")


def _probe(client, source: Path, job_id: str) -> dict:
    client.submit(
        {
            "job_id": job_id,
            "action": "file",
            "path": str(source),
            "language": "en",
            "model": "tiny",
            "config": {
                "profile": "speed",
                "transcription_backend": "groq",
                "allow_local_fallback": False,
            },
        }
    )
    result = _wait_for_error(client, job_id)
    if result.get("code") != "file_format":
        raise RuntimeError(f"Unexpected worker result: {result.get('code')}")
    return result


def run_smoke() -> None:
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps
    from PySide6.QtCore import qVersion

    from jobs import InferenceWorkerClient
    from version import APP_VERSION

    # Exercise the bundled native audio/ONNX stack without downloading a model
    # or recording a microphone. This also checks the macOS 12 dependency pins.
    if get_speech_timestamps(np.zeros(16_000, dtype=np.float32)):
        raise RuntimeError("Silence was unexpectedly classified as speech")

    temporary = tempfile.NamedTemporaryFile(prefix="whispertray-smoke-", suffix=".invalid", delete=False)
    source = Path(temporary.name)
    temporary.write(b"not audio")
    temporary.close()
    client = InferenceWorkerClient()
    first_pid = None
    second_pid = None
    try:
        _probe(client, source, "packaged-smoke-first")
        first_process = client._process
        if first_process is None or not first_process.is_alive():
            raise RuntimeError("Inference worker did not remain available after its first job")
        first_pid = first_process.pid
        client.request_cancel()
        client.terminate()
        if first_process.is_alive():
            raise RuntimeError("Inference worker did not terminate")
        _probe(client, source, "packaged-smoke-restart")
        second_process = client._process
        if second_process is None or not second_process.is_alive():
            raise RuntimeError("Restarted inference worker is unavailable")
        second_pid = second_process.pid
        if second_process is first_process:
            raise RuntimeError("Inference worker was not restarted")
    finally:
        client.shutdown()
        source.unlink(missing_ok=True)
    print(
        f"WHISPERTRAY_PACKAGED_SMOKE_OK version={APP_VERSION} qt={qVersion()} "
        f"first_pid={first_pid} second_pid={second_pid}",
        flush=True,
    )


def main() -> int | None:
    # BRIEFCASE_MAIN_MODULE is inherited by spawn and resource-tracker
    # children in --test mode. Route those children before running the smoke.
    if _run_multiprocessing_bootstrap():
        return None
    # The Windows Briefcase test launcher does not execute production main(),
    # so it must establish frozen spawn handling here as well.
    _configure_multiprocessing()
    run_smoke()
    # macOS may redact NSLog output, including Briefcase's exit sentinel.
    # A fresh, parent-owned result file verifies completion independently.
    if report_path := os.environ.get("WHISPERTRAY_SMOKE_REPORT"):
        from version import APP_VERSION

        Path(report_path).write_text(
            json.dumps({"version": APP_VERSION, "status": "passed"}), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        traceback.print_exc()
        print(">>>>>>>>>> EXIT 1 <<<<<<<<<<", flush=True)
        raise
    if exit_code is not None:
        # Briefcase test mode waits for this exact sentinel and otherwise
        # reports that the suite never produced a result.
        print(f">>>>>>>>>> EXIT {exit_code} <<<<<<<<<<", flush=True)
    raise SystemExit(exit_code or 0)
