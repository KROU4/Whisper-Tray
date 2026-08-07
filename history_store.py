"""Opt-in local transcript history with bounded retention."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config_store import app_data_dir


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "history" / "transcripts.jsonl"

    def append(self, text: str, retention_days: int = 30) -> None:
        if not text.strip():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"created_at": datetime.now(timezone.utc).isoformat(), "text": text}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.prune(retention_days)

    def prune(self, retention_days: int) -> None:
        if not self.path.exists():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        kept: list[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                if datetime.fromisoformat(entry["created_at"]) >= cutoff:
                    kept.append(json.dumps(entry, ensure_ascii=False))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
