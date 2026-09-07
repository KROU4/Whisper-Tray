"""Check an installed DEB in a clean container, without a display or microphone."""

import ctypes
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    application = Path("/usr/lib/whispertray/app")
    packages = application.parent / "app_packages"
    sys.path[:0] = [str(application), str(packages)]
    # Only the verification module comes from the checkout; runtime modules
    # must come from the files installed by apt.
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    import sounddevice
    from PySide6.QtWidgets import QApplication

    import jobs
    from release_smoke.tests.whispertray.__main__ import run_smoke

    assert Path(jobs.__file__).parent == application
    assert sounddevice.get_portaudio_version()[0] > 0
    ctypes.CDLL(str(packages / "PySide6/Qt/plugins/platforms/libqxcb.so"))
    qt_app = QApplication([])
    multiprocessing.set_executable("/usr/bin/whispertray")
    with tempfile.TemporaryDirectory(prefix="whispertray-package-check-") as profile:
        os.environ["WHISPERTRAY_DATA_DIR"] = profile
        run_smoke()
    qt_app.quit()
    print("WHISPERTRAY_INSTALLED_LINUX_OK qt_widgets=True xcb=True portaudio=True", flush=True)


if __name__ == "__main__":
    main()
