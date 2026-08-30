"""Versioned configuration with legacy migration and atomic persistence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from credentials import CredentialStore

SCHEMA_VERSION = 1
DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "profile": "privacy",
    "ui_language": "auto",
    "onboarding_complete": False,
    "start_in_tray": False,
    "language": None,
    "device_index": None,
    "hotkey": "win+alt",
    "hotkey_mode": "toggle",
    "model": "small",
    "file_model": "small",
    "transcription_backend": "local",
    "groq_model": "whisper-large-v3-turbo",
    "groq_prompt": "",
    "groq_max_retries": 4,
    "allow_local_fallback": False,
    "hud": {"enabled": True, "position": "active_monitor", "high_contrast": False, "reduce_motion": False},
    "history": {"enabled": False, "retention_days": 30},
}


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "WhisperTray"


class ConfigStore:
    def __init__(
        self, path: Path | None = None, legacy_path: Path | None = None, credentials: CredentialStore | None = None
    ):
        self.path = path or app_data_dir() / "config.json"
        self.legacy_path = legacy_path or Path(__file__).resolve().parent / "config.json"
        self.credentials = credentials or CredentialStore()

    def load(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        source = self.path if self.path.exists() else self.legacy_path
        if source.exists():
            try:
                raw = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        config, secret = self._migrate(raw)
        if secret:
            try:
                self.credentials.set_groq_key(secret)
            except RuntimeError:
                # Non-Windows tests/portable runs must not leave a plaintext copy.
                pass
        if source != self.path or raw != config:
            self.save(config)
        if secret and source == self.legacy_path:
            # Strip migrated credentials from the legacy location immediately.
            self._write_atomic(self.legacy_path, config)
        return config

    def save(self, config: dict[str, Any]) -> None:
        safe, _ = self._migrate(config)
        self._write_atomic(self.path, safe)

    @staticmethod
    def _write_atomic(path: Path, config: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _migrate(self, raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
        config = deepcopy(DEFAULT_CONFIG)
        if isinstance(raw, dict):
            config.update({key: value for key, value in raw.items() if key in config})
        secret = str(raw.get("groq_api_key", "") if isinstance(raw, dict) else "").strip()
        # A legacy explicit Groq selection remains an explicit speed choice. New installs stay local.
        if isinstance(raw, dict) and raw.get("transcription_backend") == "groq":
            config["profile"], config["transcription_backend"] = "speed", "groq"
        if config["profile"] not in {"privacy", "speed"}:
            config["profile"] = "privacy"
        config["groq_max_retries"] = min(max(int(config.get("groq_max_retries", 4)), 0), 8)
        if config.get("hotkey_mode") not in {"toggle", "hold"}:
            config["hotkey_mode"] = "toggle"
        try:
            from platform_integration import normalize_hotkey

            config["hotkey"] = normalize_hotkey(str(config.get("hotkey", DEFAULT_CONFIG["hotkey"])))
        except Exception:
            config["hotkey"] = DEFAULT_CONFIG["hotkey"]
        history = config.get("history") if isinstance(config.get("history"), dict) else {}
        config["history"] = {
            "enabled": bool(history.get("enabled", False)),
            "retention_days": min(max(int(history.get("retention_days", 30)), 1), 365),
        }
        hud = config.get("hud") if isinstance(config.get("hud"), dict) else {}
        config["hud"] = {
            "enabled": bool(hud.get("enabled", True)),
            "position": hud.get("position")
            if hud.get("position") in {"bottom_left", "bottom_right", "active_monitor"}
            else "bottom_right",
            "high_contrast": bool(hud.get("high_contrast", False)),
            "reduce_motion": bool(hud.get("reduce_motion", False)),
        }
        config["transcription_backend"] = "local" if config["profile"] == "privacy" else "groq"
        config["allow_local_fallback"] = bool(config.get("allow_local_fallback", False))
        config["start_in_tray"] = bool(config.get("start_in_tray", False))
        config["schema_version"] = SCHEMA_VERSION
        return config, secret
