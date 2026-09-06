"""Privacy-safe application logging shared by the desktop entry point and UI."""

from __future__ import annotations

import logging
import re
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SENSITIVE_VALUE = re.compile(r"(?i)(groq_api_key|api[_-]?key|authorization|bearer)\s*([:=])\s*([^\s,;]+)")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;'\"]+")
_GROQ_KEY = re.compile(r"gsk_[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    return _GROQ_KEY.sub("[REDACTED]", _SENSITIVE_VALUE.sub(r"\1\2 [REDACTED]", text))


class SensitiveDataFilter(logging.Filter):
    """Remove credentials from all handlers; transcript content is never logged by callers."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = redact(rendered)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
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
    for old_handler in root.handlers[:]:
        root.removeHandler(old_handler)
        old_handler.close()
    root.addHandler(handler)
    return log_file
