"""Opt-in local transcript history with bounded retention."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config_store import app_data_dir

_HISTORY_LOCK = threading.RLock()


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "history" / "transcripts.jsonl"

    def append(self, text: str, retention_days: int = 30) -> None:
        if not text.strip():
            return
        with _HISTORY_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"created_at": datetime.now(timezone.utc).isoformat(), "text": text}
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.prune(retention_days)

    def prune(self, retention_days: int) -> None:
        with _HISTORY_LOCK:
            if not self.path.exists():
                return
            cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
            kept: list[str] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    created_at = datetime.fromisoformat(entry["created_at"])
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    if created_at >= cutoff and isinstance(entry["text"], str):
                        kept.append(json.dumps(entry, ensure_ascii=False))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
            temporary.replace(self.path)

    def entries(self, query: str = "", limit: int = 250) -> list[dict[str, str]]:
        """Return newest-first valid entries, optionally filtered by text."""
        with _HISTORY_LOCK:
            if not self.path.exists():
                return []
            needle = query.strip().casefold()
            result: list[dict[str, str]] = []
            for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
                try:
                    entry = json.loads(line)
                    created_at, text = entry["created_at"], entry["text"]
                    datetime.fromisoformat(created_at)
                    if not isinstance(text, str) or (needle and needle not in text.casefold()):
                        continue
                    result.append({"created_at": created_at, "text": text})
                    if len(result) >= max(1, limit):
                        break
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            return result

    def clear(self) -> None:
        with _HISTORY_LOCK:
            self.path.unlink(missing_ok=True)
