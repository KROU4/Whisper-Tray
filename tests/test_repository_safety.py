from pathlib import Path

from config_store import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_is_safe_and_privacy_first():
    assert DEFAULT_CONFIG["profile"] == "privacy"
    assert DEFAULT_CONFIG["transcription_backend"] == "local"
    assert "groq_api_key" not in DEFAULT_CONFIG


def test_local_config_and_logs_are_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "config.json" in ignore
    assert "*.log" in ignore


def test_every_root_module_is_packaged():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    sources = set(config["tool"]["briefcase"]["app"]["whispertray"]["sources"])
    modules = {path.name for path in root.glob("*.py")}
    assert modules - sources == set(), "Root modules missing from Briefcase sources"
