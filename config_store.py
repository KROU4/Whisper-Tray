"""Versioned configuration with legacy migration and atomic persistence."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from credentials import CredentialStore

SCHEMA_VERSION = 2
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
    "groq_max_retries": 2,
    "allow_local_fallback": False,
    "hud": {"enabled": True, "position": "active_monitor", "high_contrast": False, "reduce_motion": False},
    "history": {"enabled": False, "retention_days": 30},
}


def app_data_dir() -> Path:
    override = os.environ.get("WHISPERTRAY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
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
        self.recovery_notice: str | None = None

    def load(self) -> dict[str, Any]:
        self.recovery_notice = None
        raw: dict[str, Any] = {}
        source = self.path if self.path.exists() else self.legacy_path
        if source.exists():
            try:
                raw = json.loads(source.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Configuration root must be an object")
            except (UnicodeError, ValueError):
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                try:
                    shutil.copy2(source, source.with_name(f"config.corrupt-{stamp}.json"))
                except OSError:
                    # Preserve the original if a recoverable backup cannot be made.
                    self.recovery_notice = "config_backup_failed"
                    return deepcopy(DEFAULT_CONFIG)
                self.recovery_notice = "config_recovered"
                raw = {}
            except OSError:
                # Do not overwrite an unreadable file with defaults.
                self.recovery_notice = "config_unavailable"
                return deepcopy(DEFAULT_CONFIG)
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
        # Only pre-profile settings may infer a profile from the legacy backend.
        if isinstance(raw, dict) and "profile" not in raw and raw.get("schema_version", 0) in (0, 1) and raw.get("transcription_backend") == "groq":
            config["profile"], config["transcription_backend"] = "speed", "groq"
        if config["profile"] not in ("privacy", "speed"):
            config["profile"] = "privacy"
        config["groq_max_retries"] = bounded_int(config.get("groq_max_retries"), 2, 0, 2)
        if config.get("hotkey_mode") not in ("toggle", "hold"):
            config["hotkey_mode"] = "toggle"
        try:
            from platform_integration import normalize_hotkey

            config["hotkey"] = normalize_hotkey(str(config.get("hotkey", DEFAULT_CONFIG["hotkey"])))
        except Exception:
            config["hotkey"] = DEFAULT_CONFIG["hotkey"]
        history = config.get("history") if isinstance(config.get("history"), dict) else {}
        config["history"] = {
            "enabled": safe_bool(history.get("enabled"), False),
            "retention_days": bounded_int(history.get("retention_days"), 30, 1, 365),
        }
        hud = config.get("hud") if isinstance(config.get("hud"), dict) else {}
        config["hud"] = {
            "enabled": safe_bool(hud.get("enabled"), True),
            "position": hud.get("position")
            if hud.get("position") in ("bottom_left", "bottom_right", "active_monitor")
            else "bottom_right",
            "high_contrast": safe_bool(hud.get("high_contrast"), False),
            "reduce_motion": safe_bool(hud.get("reduce_motion"), False),
        }
        config["transcription_backend"] = "local" if config["profile"] == "privacy" else "groq"
        for key in ("allow_local_fallback", "start_in_tray", "onboarding_complete"):
            config[key] = safe_bool(config.get(key), False)
        if config.get("ui_language") not in ("auto", "ru", "en"):
            config["ui_language"] = "auto"
        if config.get("language") not in (None, "ru", "en"):
            config["language"] = None
        for key in ("model", "file_model"):
            if config.get(key) not in ("tiny", "base", "small", "medium", "large"):
                config[key] = "small"
        if not isinstance(config.get("device_index"), int) or isinstance(config.get("device_index"), bool) or config["device_index"] < 0:
            config["device_index"] = None
        if not isinstance(config.get("groq_prompt"), str):
            config["groq_prompt"] = ""
        if config.get("groq_model") not in ("whisper-large-v3-turbo", "whisper-large-v3"):
            config["groq_model"] = DEFAULT_CONFIG["groq_model"]
        config["schema_version"] = SCHEMA_VERSION
        return config, secret


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(max(int(value), minimum), maximum)
    except (TypeError, ValueError, OverflowError):
        return default


def safe_bool(value: Any, default: bool) -> bool:
    return bool(value) if isinstance(value, (bool, int)) and value in (0, 1) else default
