import json

from diagnostics import collect_diagnostics, export_diagnostics, safe_config_summary


def test_safe_config_summary_excludes_credentials():
    config = {
        "profile": "speed",
        "groq_api_key": "do-not-export",
        "api_key": "also-secret",
        "hotkey": "win+alt",
    }

    assert safe_config_summary(config) == {"profile": "speed", "hotkey": "win+alt"}


def test_diagnostics_are_anonymized_and_exported_atomically(tmp_path):
    destination = tmp_path / "support" / "diagnostics.json"
    output = export_diagnostics(
        destination,
        {"transcription_backend": "groq", "groq_api_key": "secret-value", "model": "small"},
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert output == destination
    assert data["backend"] == "groq"
    assert data["groq_key_configured"] is True
    assert "groq_api_key" not in data["config"]
    assert "secret-value" not in output.read_text(encoding="utf-8")
    assert not destination.with_suffix(".json.tmp").exists()


def test_collect_diagnostics_uses_explicit_backend(monkeypatch):
    monkeypatch.setattr("credentials.CredentialStore.get_groq_key", lambda _self: "")

    snapshot = collect_diagnostics({"transcription_backend": "local"}, backend="groq")

    assert snapshot["backend"] == "groq"
    assert snapshot["groq_key_configured"] is False
