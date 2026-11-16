from datetime import datetime

from watcher.models import RunResult, failed_result
from watcher.store import Store


def test_record_and_seen():
    s = Store(":memory:")
    assert not s.seen("w", "u")
    s.record("w", "u", 20000)
    assert s.seen("w", "u") and s.last_price("w", "u") == 20000
    s.record("w", "u", 18000)
    assert s.last_price("w", "u") == 18000
    assert not s.seen("other", "u")


def test_record_none_price():
    s = Store(":memory:")
    s.record("w", "u", None)
    assert s.seen("w", "u") and s.last_price("w", "u") is None


def test_status_and_ever_succeeded():
    s = Store(":memory:")
    assert not s.ever_succeeded("w") and s.last_status("w") is None
    s.set_status("w", "failed")
    assert s.last_status("w") == "failed" and not s.ever_succeeded("w")
    s.set_status("w", "ok")
    assert s.last_status("w") == "ok" and s.ever_succeeded("w")
    s.set_status("w", "failed")
    assert s.ever_succeeded("w")


def test_last_run_roundtrip():
    s = Store(":memory:")
    assert s.last_run("w") is None
    t = datetime(2026, 11, 16, 9, 30)
    s.set_last_run("w", t)
    assert s.last_run("w") == t


def test_persists_across_instances(tmp_path):
    p = tmp_path / "state.db"
    Store(p).record("w", "u", 1)
    assert Store(p).seen("w", "u")


def test_run_result_failed():
    ok = RunResult(searched=True, listings_seen=0, blocked_or_lost=False, note="", listings=[])
    assert ok.failed is False
    assert RunResult(searched=True, listings_seen=0, blocked_or_lost=True, note="", listings=[]).failed
    assert failed_result("x").failed is True


def test_record_key():
    s = Store(":memory:")
    s.record("w", "u", 100, key="k")
    assert s.seen_key("w", "k") and s.key_price("w", "k") == 100
    s.record("w", "u2", 90, key="k")
    assert s.key_price("w", "k") == 90
    assert not s.seen_key("w", "other")


def test_old_db_without_keys_table(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript(
        "CREATE TABLE seen (watch TEXT, url TEXT, price INTEGER, first_seen TEXT, PRIMARY KEY (watch, url));"
        "CREATE TABLE watch_state (watch TEXT PRIMARY KEY, status TEXT, ever_ok INTEGER DEFAULT 0, last_run TEXT);"
    )
    db.close()
    s = Store(p)
    assert s.seen_key("w", "k") is False
    s.record("w", "u", 1, key="k")
    assert s.seen_key("w", "k")
