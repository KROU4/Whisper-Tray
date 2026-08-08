"""Safe diagnostic data and export helpers.

The module deliberately exposes environment and configuration metadata only;
it never includes API keys, audio, transcript text, or local history.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_VERSION = "1.1.0"
_SECRET_KEYS = frozenset({"groq_api_key", "api_key", "token", "password", "secret"})


def safe_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Return config fields suitable for a support bundle."""
    return {
        key: value
        for key, value in config.items()
        if key.lower() not in _SECRET_KEYS and not key.lower().endswith(("_api_key", "_token", "_secret", "_password"))
    }


def collect_diagnostics(config: dict[str, Any], *, backend: str | None = None) -> dict[str, Any]:
    """Create a JSON-serializable, anonymized diagnostic snapshot."""
    selected_backend = backend or str(config.get("transcription_backend", "local"))
    key_configured = bool(config.get("groq_api_key"))
    if not key_configured:
        try:
            from credentials import CredentialStore

            key_configured = bool(CredentialStore().get_groq_key())
        except Exception:
            key_configured = False
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "backend": selected_backend,
        "groq_key_configured": key_configured,
        "config": safe_config_summary(config),
    }


def export_diagnostics(destination: Path, config: dict[str, Any], *, backend: str | None = None) -> Path:
    """Write an anonymized diagnostic JSON atomically and return its path."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(collect_diagnostics(config, backend=backend), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination
