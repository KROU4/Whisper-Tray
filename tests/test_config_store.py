import json

from config_store import ConfigStore


class FakeCredentials:
    def __init__(self):
        self.stored = []

    def set_groq_key(self, value):
        self.stored.append(value)


def test_legacy_config_is_migrated_without_persisting_secret(tmp_path):
    legacy = tmp_path / "legacy-config.json"
    target = tmp_path / "data" / "config.json"
    legacy.write_text(
        json.dumps(
            {
                "transcription_backend": "groq",
                "groq_api_key": "legacy-secret",
                "hotkey": "ctrl+space",
            }
        ),
        encoding="utf-8",
    )
    credentials = FakeCredentials()

    config = ConfigStore(target, legacy, credentials).load()

    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert config["profile"] == "speed"
    assert config["hotkey"] == "ctrl+space"
    assert credentials.stored == ["legacy-secret"]
    assert "groq_api_key" not in persisted
    assert "legacy-secret" not in target.read_text(encoding="utf-8")
    assert "legacy-secret" not in legacy.read_text(encoding="utf-8")


def test_new_config_defaults_to_private_local_profile(tmp_path):
    target = tmp_path / "data" / "config.json"

    config = ConfigStore(target, tmp_path / "missing.json", FakeCredentials()).load()

    assert config["profile"] == "privacy"
    assert config["transcription_backend"] == "local"
    assert config["onboarding_complete"] is False
    assert config["start_in_tray"] is False
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1


def test_onboarding_completion_is_persisted(tmp_path):
    target = tmp_path / "data" / "config.json"
    store = ConfigStore(target, tmp_path / "missing.json", FakeCredentials())
    config = store.load()
    config["onboarding_complete"] = True
    store.save(config)

    assert store.load()["onboarding_complete"] is True


def test_invalid_bounded_values_are_normalized(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy.json", FakeCredentials())
    store.save(
        {
            "profile": "privacy",
            "groq_max_retries": 99,
            "hotkey_mode": "invalid",
            "history": {"enabled": 1, "retention_days": 9999},
            "hud": {"position": "nowhere"},
        }
    )
    config = store.load()
    assert config["groq_max_retries"] == 8
    assert config["hotkey_mode"] == "toggle"
    assert config["history"] == {"enabled": True, "retention_days": 365}
    assert config["hud"]["position"] == "bottom_right"


def test_explicit_local_fallback_consent_is_persisted(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy.json", FakeCredentials())
    store.save({"profile": "speed", "allow_local_fallback": True})
    assert store.load()["allow_local_fallback"] is True


def test_hotkey_is_serialized_in_machine_format(tmp_path):
    target = tmp_path / "config.json"
    store = ConfigStore(target, tmp_path / "missing.json", FakeCredentials())

    store.save({"hotkey": "Control + Shift + K"})

    assert store.load()["hotkey"] == "ctrl+shift+k"
    assert json.loads(target.read_text(encoding="utf-8"))["hotkey"] == "ctrl+shift+k"


def test_start_in_tray_preference_is_persisted(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy.json", FakeCredentials())
    store.save({"profile": "privacy", "start_in_tray": True})
    assert store.load()["start_in_tray"] is True


def test_save_is_atomic_and_never_serializes_secret(tmp_path):
    target = tmp_path / "data" / "config.json"
    store = ConfigStore(target, tmp_path / "missing.json", FakeCredentials())

    store.save({"profile": "speed", "groq_api_key": "must-not-be-written"})

    data = target.read_text(encoding="utf-8")
    assert "must-not-be-written" not in data
    assert "groq_api_key" not in data
    assert not list(target.parent.glob("config-*.tmp"))
