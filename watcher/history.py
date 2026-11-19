"""Saved conversations in a local SQLite file (stdlib only). One writer: the web server."""
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / ".thrift-scout" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conv_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('you', 'agent')),
    text TEXT NOT NULL,
    listings_json TEXT NOT NULL DEFAULT '[]',
    steps INTEGER, cost_usd REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id, id);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class History:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)

    def new_conversation(self, title: str) -> str:
        conv, now = uuid.uuid4().hex, _now()
        with self.db:
            self.db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?)", (conv, title.strip()[:60], now, now))
        return conv

    def add_message(self, conv: str, role: str, text: str, listings: list[dict] | None = None,
                    steps: int | None = None, cost: float | None = None) -> None:
        now = _now()
        with self.db:
            self.db.execute(
                "INSERT INTO messages (conv_id, role, text, listings_json, steps, cost_usd, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv, role, text, json.dumps(listings or [], ensure_ascii=False), steps, cost, now),
            )
            self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv))

    def messages(self, conv: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, text, listings_json, steps, cost_usd FROM messages WHERE conv_id = ? ORDER BY id", (conv,)
        )
        return [{"role": r, "text": t, "listings": json.loads(j), "steps": s, "cost": c} for r, t, j, s, c in rows]

    def conversations(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [{"conv": i, "title": t, "updated": u} for i, t, u in rows]

    def latest(self) -> str | None:
        row = self.db.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def exists(self, conv: str) -> bool:
        return self.db.execute("SELECT 1 FROM conversations WHERE id = ?", (conv,)).fetchone() is not None


def recap(messages: list[dict], limit: int = 12) -> str:
    """Text summary of earlier turns, given to a fresh agent when a saved conversation is resumed."""
    if not messages:
        return ""
    lines = ["Earlier in this conversation (you may need to reopen pages to check details):"]
    for m in messages[-limit:]:
        lines.append(("User: " if m["role"] == "you" else "You answered: ") + m["text"][:500])
        for n, item in enumerate(m["listings"], 1):
            price = "?" if item.get("price") is None else f"{item['price']:,}"
            lines.append(f"  #{n} ¥{price} {item.get('title', '')} {item.get('url', '')}")
    return "\n".join(lines)
