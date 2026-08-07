import json
from datetime import datetime, timedelta, timezone

from history_store import HistoryStore


def test_history_can_be_written_and_cleared(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append("Private transcript", 30)
    entry = json.loads(store.path.read_text(encoding="utf-8"))
    assert entry["text"] == "Private transcript"
    store.clear()
    assert not store.path.exists()


def test_history_prunes_expired_and_invalid_rows(tmp_path):
    path = tmp_path / "history.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps({"created_at": old, "text": "old"})
        + "\n"
        + "invalid\n"
        + json.dumps({"created_at": fresh, "text": "fresh"})
        + "\n",
        encoding="utf-8",
    )
    HistoryStore(path).prune(30)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["fresh"]
