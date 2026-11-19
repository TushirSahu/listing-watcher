"""Small persistent cache for slow lookups (train routes, branch addresses): one SQLite file, entries expire after
30 days. Path: $CACHE_DB, else ~/.thrift-scout/cache.db. A broken cache file never breaks a lookup."""
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

MAX_AGE = 30 * 24 * 3600  # seconds


def _db() -> sqlite3.Connection:
    path = Path(os.environ.get("CACHE_DB") or Path.home() / ".thrift-scout" / "cache.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS cache (kind TEXT, key TEXT, value TEXT, saved REAL, PRIMARY KEY (kind, key))")
    return db


def get(kind: str, key, max_age: float = MAX_AGE):
    """The stored value (JSON-decoded), or None when missing, older than max_age seconds, or the cache can't be read."""
    try:
        with closing(_db()) as db:
            row = db.execute("SELECT value FROM cache WHERE kind = ? AND key = ? AND saved > ?",
                             (kind, json.dumps(key), time.time() - max_age)).fetchone()
    except (sqlite3.Error, OSError):
        return None
    return json.loads(row[0]) if row else None


def put(kind: str, key, value) -> None:
    try:
        with closing(_db()) as db, db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
                       (kind, json.dumps(key), json.dumps(value), time.time()))
    except (sqlite3.Error, OSError):
        pass  # note: not cached this time; the lookup still answers
