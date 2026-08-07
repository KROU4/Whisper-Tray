"""Privacy-safe application logging shared by the desktop entry point and UI."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SENSITIVE_VALUE = re.compile(r"(?i)(groq_api_key|api[_-]?key|authorization|bearer)\s*([:=])\s*([^\s,;]+)")


class SensitiveDataFilter(logging.Filter):
    """Remove credentials from all handlers; transcript content is never logged by callers."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = _SENSITIVE_VALUE.sub(r"\1\2 [REDACTED]", rendered)
        record.args = ()
        return True


def configure_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """Configure bounded file logging and return the active log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "whisper_tray.log"
    handler = RotatingFileHandler(log_file, encoding="utf-8", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    handler.addFilter(SensitiveDataFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    return log_file
