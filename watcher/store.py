import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    watch TEXT, url TEXT, price INTEGER, first_seen TEXT,
    PRIMARY KEY (watch, url)
);
CREATE TABLE IF NOT EXISTS watch_state (
    watch TEXT PRIMARY KEY, status TEXT, ever_ok INTEGER DEFAULT 0, last_run TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def _one(self, sql: str, *args):
        return self.db.execute(sql, args).fetchone()

    def _write(self, sql: str, *args) -> None:
        with self.db:
            self.db.execute(sql, args)

    def seen(self, watch: str, url: str) -> bool:
        return self._one("SELECT 1 FROM seen WHERE watch=? AND url=?", watch, url) is not None

    def last_price(self, watch: str, url: str) -> int | None:
        row = self._one("SELECT price FROM seen WHERE watch=? AND url=?", watch, url)
        return row[0] if row else None

    def record(self, watch: str, url: str, price: int | None) -> None:
        self._write(
            "INSERT INTO seen (watch, url, price, first_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(watch, url) DO UPDATE SET price=excluded.price",
            watch, url, price, datetime.now().isoformat(),
        )

    def _state(self, watch: str):
        return self._one("SELECT status, ever_ok, last_run FROM watch_state WHERE watch=?", watch)

    def ever_succeeded(self, watch: str) -> bool:
        row = self._state(watch)
        return bool(row and row[1])

    def last_status(self, watch: str) -> str | None:
        row = self._state(watch)
        return row[0] if row else None

    def set_status(self, watch: str, status: str) -> None:
        ok = 1 if status == "ok" else 0
        self._write(
            "INSERT INTO watch_state (watch, status, ever_ok) VALUES (?, ?, ?) "
            "ON CONFLICT(watch) DO UPDATE SET status=excluded.status, ever_ok=max(ever_ok, excluded.ever_ok)",
            watch, status, ok,
        )

    def last_run(self, watch: str) -> datetime | None:
        row = self._state(watch)
        return datetime.fromisoformat(row[2]) if row and row[2] else None

    def set_last_run(self, watch: str, when: datetime) -> None:
        self._write(
            "INSERT INTO watch_state (watch, last_run) VALUES (?, ?) "
            "ON CONFLICT(watch) DO UPDATE SET last_run=excluded.last_run",
            watch, when.isoformat(),
        )
