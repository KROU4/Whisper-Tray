import sys
from types import SimpleNamespace

import autostart
import credentials
import platform_integration


class FakeKey:
    ctrl = "CTRL"
    alt = "ALT"
    shift = "SHIFT"
    cmd = "CMD"
    space = "SPACE"
    enter = "ENTER"
    esc = "ESC"


class FakeKeyCode:
    @staticmethod
    def from_char(value):
        return f"char:{value}"


def fake_keyboard():
    return SimpleNamespace(Key=FakeKey, KeyCode=FakeKeyCode)


def test_parse_hotkey_is_cross_platform_and_rejects_invalid(monkeypatch):
    monkeypatch.setattr(platform_integration, "_pynput_keyboard", fake_keyboard)
    assert platform_integration.parse_hotkey("ctrl+space") == ("CTRL", "SPACE")
    assert platform_integration.parse_hotkey("win+a") == ("CMD", "char:a")
    try:
        platform_integration.parse_hotkey("ctrl+nope")
    except platform_integration.PlatformIntegrationError as exc:
        assert exc.code == "invalid_hotkey"
    else:
        raise AssertionError("invalid shortcut was accepted")


def test_global_hotkey_hold_fires_once_and_stops_on_final_release(monkeypatch):
    monkeypatch.setattr(platform_integration, "_pynput_keyboard", fake_keyboard)
    fired, released = [], []
    hotkey = platform_integration.GlobalHotkey("ctrl+space", lambda: fired.append(True), lambda: released.append(True))
    hotkey._keys = platform_integration.parse_hotkey("ctrl+space")
    hotkey._on_press("CTRL")
    hotkey._on_press("SPACE")
    hotkey._on_press("SPACE")
    hotkey._on_release("SPACE")
    assert fired == [True]
    assert released == [True]


def test_text_inserter_uses_clipboard_when_input_permission_fails(monkeypatch):
    class BrokenController:
        def type(self, _text):
            raise RuntimeError("Accessibility denied")

    monkeypatch.setitem(sys.modules, "pyperclip", SimpleNamespace(copy=lambda text: text))
    assert platform_integration.TextInserter(SimpleNamespace(Controller=BrokenController)).insert("text") == "clipboard"


def test_keyring_is_used_for_secure_cross_platform_storage(monkeypatch):
    values = {}
    fake_keyring = SimpleNamespace(
        get_password=lambda service, name: values.get((service, name)),
        set_password=lambda service, name, value: values.__setitem__((service, name), value),
        delete_password=lambda service, name: values.pop((service, name), None),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    store = credentials.CredentialStore()
    store.set_groq_key(" test-key ")
    assert store.get_groq_key() == "test-key"
    store.delete_groq_key()
    assert store.get_groq_key() == ""


def test_linux_autostart_creates_and_removes_desktop_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(autostart, "_launch_args", lambda: ["/opt/whispertray/WhisperTray"])
    assert autostart.enable() is True
    target = tmp_path / "config" / "autostart" / "whispertray.desktop"
    assert target.exists() and "Exec=/opt/whispertray/WhisperTray" in target.read_text(encoding="utf-8")
    assert autostart.is_enabled() is True
    assert autostart.disable() is True
    assert not target.exists()
