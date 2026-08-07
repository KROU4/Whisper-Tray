import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_safe_and_privacy_first():
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

    assert config["profile"] == "privacy"
    assert config["transcription_backend"] == "local"
    assert "groq_api_key" not in config


def test_local_config_and_logs_are_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "config.json" in ignore
    assert "*.log" in ignore
