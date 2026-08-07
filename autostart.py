"""Per-user autostart registration for Windows, macOS and Linux desktops."""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "WhisperTray"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_MAC_LABEL = "com.whispertray.app"


def _launch_args() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    executable = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    return [str(executable), str(Path(__file__).with_name("main.py"))]


def _get_run_value() -> str:
    """Windows registry command, retained as a small public compatibility API."""
    return subprocess_list2cmdline(_launch_args())


def subprocess_list2cmdline(args: list[str]) -> str:
    if sys.platform == "win32":
        from subprocess import list2cmdline

        return list2cmdline(args)
    return " ".join(shlex.quote(arg) for arg in args)


def _linux_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "whispertray.desktop"


def _mac_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"


def _enable_windows() -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_run_value())


def _disable_windows() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass


def _enable_linux() -> None:
    target = _linux_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(shlex.quote(arg) for arg in _launch_args())
    target.write_text(
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        f"Name={APP_NAME}\nComment=Voice dictation\nExec={command}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )


def _enable_macos() -> None:
    import plistlib

    target = _mac_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        plistlib.dump({"Label": _MAC_LABEL, "ProgramArguments": _launch_args(), "RunAtLoad": True}, handle)


def enable() -> bool:
    """Enable per-user autostart. Returns False and logs a visible failure reason."""
    try:
        if sys.platform == "win32":
            _enable_windows()
        elif sys.platform == "darwin":
            _enable_macos()
        elif sys.platform.startswith("linux"):
            _enable_linux()
        else:
            raise RuntimeError(f"Autostart is unsupported on {sys.platform}")
        return True
    except Exception as exc:
        logger.error("Could not enable autostart: %s", exc)
        return False


def disable() -> bool:
    try:
        if sys.platform == "win32":
            _disable_windows()
        elif sys.platform == "darwin":
            _mac_file().unlink(missing_ok=True)
        elif sys.platform.startswith("linux"):
            _linux_file().unlink(missing_ok=True)
        else:
            raise RuntimeError(f"Autostart is unsupported on {sys.platform}")
        return True
    except Exception as exc:
        logger.error("Could not disable autostart: %s", exc)
        return False


def is_enabled() -> bool:
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        if sys.platform == "darwin":
            return _mac_file().is_file()
        if sys.platform.startswith("linux"):
            return _linux_file().is_file()
    except (FileNotFoundError, OSError):
        return False
    return False
