"""Small, explicit adapters for desktop integrations across supported OSes.

The application keeps OS APIs behind this module so importing it never fails on
another platform.  ``pynput`` supplies the global keyboard implementation on
Windows, macOS and X11 Linux.  On Wayland it cannot provide global shortcuts;
the caller receives a useful error instead of pretending that the shortcut is
active.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable


class PlatformIntegrationError(RuntimeError):
    """A recoverable OS integration failure safe to show to a user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _pynput_keyboard():
    try:
        from pynput import keyboard

        return keyboard
    except Exception as exc:  # includes unavailable macOS Accessibility/X server
        if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY"):
            detail = "Global shortcuts are unavailable in this Wayland session. Use an X11 session."
        elif sys.platform == "darwin":
            detail = "Allow WhisperTray in macOS Privacy & Security > Accessibility."
        else:
            detail = "Install pynput and allow global input access for WhisperTray."
        raise PlatformIntegrationError("global_input_unavailable", detail) from exc


def _key_for_name(keyboard, name: str):
    normalized = name.strip().lower()
    aliases = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "win": "cmd",
        "windows": "cmd",
        "super": "cmd",
        "command": "cmd",
        "cmd": "cmd",
        "option": "alt",
        "return": "enter",
        "esc": "esc",
    }
    normalized = aliases.get(normalized, normalized)
    key = getattr(keyboard.Key, normalized, None)
    if key is not None:
        return key
    if len(normalized) == 1:
        return keyboard.KeyCode.from_char(normalized)
    if normalized.startswith("f") and normalized[1:].isdigit():
        key = getattr(keyboard.Key, normalized, None)
        if key is not None:
            return key
    raise PlatformIntegrationError("invalid_hotkey", f"Unsupported hotkey key: {name}")


def normalize_hotkey(value: str) -> str:
    """Return the stable, serialization-friendly shortcut representation."""
    aliases = {
        "control": "ctrl",
        "option": "alt",
        "windows": "win",
        "super": "win",
        "command": "cmd",
        "return": "enter",
        "escape": "esc",
        "pgup": "page_up",
        "pgdown": "page_down",
    }
    parts = [aliases.get(item.strip().lower(), item.strip().lower()) for item in value.split("+") if item.strip()]
    if not parts:
        raise PlatformIntegrationError("invalid_hotkey", "Choose a shortcut such as Ctrl+Space.")
    return "+".join(parts)


def parse_hotkey(value: str):
    """Return a canonical tuple consumed by :class:`GlobalHotkey`.

    Deliberately accepts the existing ``ctrl+space`` configuration format.
    """
    keyboard = _pynput_keyboard()
    parts = normalize_hotkey(value).split("+")
    keys = tuple(_key_for_name(keyboard, part) for part in parts)
    if len(set(keys)) != len(keys):
        raise PlatformIntegrationError("invalid_hotkey", "Each shortcut key may be used only once.")
    return keys


class GlobalHotkey:
    """A registered global shortcut, including press-and-hold mode."""

    def __init__(self, shortcut: str, on_activate: Callable[[], None], on_release: Callable[[], None] | None = None):
        self.shortcut = shortcut
        self.on_activate = on_activate
        self.on_release = on_release
        self._keys = ()
        self._pressed = set()
        self._active = False
        self._listener = None

    @property
    def final_key(self):
        return self._keys[-1]

    def start(self) -> None:
        keyboard = _pynput_keyboard()
        try:
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            # Listener events are canonicalized by the platform backend. Apply
            # the same transformation to every configured key (including the
            # non-modifier key: Windows turns Key.space into KeyCode(32)).
            self._keys = tuple(self._listener.canonical(key) for key in parse_hotkey(self.shortcut))
            self._listener.start()
        except Exception as exc:
            self._listener = None
            raise PlatformIntegrationError("hotkey_registration_failed", "Could not register the global shortcut.") from exc

    def _on_press(self, key) -> None:
        key = self._canonical(key)
        self._pressed.add(key)
        if not self._active and all(item in self._pressed for item in self._keys):
            self._active = True
            self.on_activate()

    def _on_release(self, key) -> None:
        key = self._canonical(key)
        should_release = self._active and key == self.final_key and self.on_release is not None
        self._pressed.discard(key)
        if should_release:
            self.on_release()
        if not all(item in self._pressed for item in self._keys):
            self._active = False

    def _canonical(self, key):
        """Normalise listener events before comparing them with configured keys.

        ``pynput`` emits side-specific modifiers (for example ``ctrl_l``),
        while parsed shortcuts intentionally use the platform-neutral
        ``ctrl``.  Its own ``GlobalHotKeys`` wrapper calls this same listener
        method before updating key state.
        """
        if self._listener is not None:
            return self._listener.canonical(key)
        return key

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pressed.clear()
        self._active = False


class TextInserter:
    """Inject text through pynput; use clipboard only as an explicit fallback."""

    def __init__(self, keyboard_module=None):
        self._keyboard = keyboard_module

    def insert(self, text: str) -> str:
        if not text:
            return "failed"
        try:
            keyboard = self._keyboard or _pynput_keyboard()
            keyboard.Controller().type(text)
            return "inserted"
        except PlatformIntegrationError:
            return self._copy_fallback(text)
        except Exception:
            return self._copy_fallback(text)

    @staticmethod
    def _copy_fallback(text: str) -> str:
        try:
            import pyperclip

            pyperclip.copy(text)
            return "clipboard"
        except Exception:
            return "failed"
