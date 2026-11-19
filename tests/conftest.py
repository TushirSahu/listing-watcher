import pytest


@pytest.fixture(autouse=True)
def _cache_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path / "cache.db"))  # never touch the real ~/.listing-watcher
