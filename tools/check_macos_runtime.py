"""Run the native test bundle without relying on macOS system-log visibility."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    executable = root / "build/whispertray/macos/app/WhisperTray.app/Contents/MacOS/WhisperTray"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with tempfile.TemporaryDirectory(prefix="whispertray-package-check-") as temporary:
        report = Path(temporary) / "result.json"
        environment = os.environ.copy()
        environment.update(
            BRIEFCASE_MAIN_MODULE="tests.whispertray",
            WHISPERTRAY_DATA_DIR=str(Path(temporary) / "profile"),
            WHISPERTRAY_SMOKE_REPORT=str(report),
        )
        # Running the actual Mach-O launcher also exercises the executable
        # inherited by spawned workers. Exit zero alone is insufficient.
        subprocess.run([str(executable)], env=environment, check=True, timeout=120)
        result = json.loads(report.read_text(encoding="utf-8"))
        if result != {"version": "1.2.0", "status": "passed"}:
            raise RuntimeError(f"Unexpected packaged smoke result: {result}")
    print("WHISPERTRAY_MACOS_RUNTIME_OK version=1.2.0 vad=True worker_restart=True")


if __name__ == "__main__":
    main()
