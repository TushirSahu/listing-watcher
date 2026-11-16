from datetime import timedelta
from pathlib import Path

import pytest

from watcher.config import ConfigError, Watch, load_watches, parse_interval

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, text):
    p = tmp_path / "watches.yaml"
    p.write_text(text)
    return p


def test_parse_interval():
    assert parse_interval("15m") == timedelta(minutes=15)
    assert parse_interval("2h") == timedelta(hours=2)
    assert parse_interval("1d") == timedelta(days=1)


@pytest.mark.parametrize("bad", ["10m", "2 hours", "h", "", "0h"])
def test_parse_interval_rejects(bad):
    with pytest.raises(ConfigError):
        parse_interval(bad)


def test_load_defaults(tmp_path):
    p = write(tmp_path, "watches:\n  - name: a\n    site: https://x.jp\n    want: thing\n")
    [w] = load_watches(p)
    assert w == Watch(name="a", site="https://x.jp", want="thing", max_price=None,
                      every=timedelta(hours=2), max_results=10, open_listings=5)


def test_missing_field_names_watch(tmp_path):
    p = write(tmp_path, "watches:\n  - name: phones\n    site: https://x.jp\n")
    with pytest.raises(ConfigError) as e:
        load_watches(p)
    assert "phones" in str(e.value)


def test_bad_interval_names_watch(tmp_path):
    p = write(tmp_path, "watches:\n  - name: phones\n    site: https://x.jp\n    want: t\n    every: 2 hours\n")
    with pytest.raises(ConfigError) as e:
        load_watches(p)
    assert "phones" in str(e.value)


def test_duplicate_names(tmp_path):
    p = write(tmp_path, "watches:\n  - {name: a, site: s, want: w}\n  - {name: a, site: s, want: w}\n")
    with pytest.raises(ConfigError):
        load_watches(p)


def test_example_file_loads():
    [w] = load_watches(ROOT / "watches.example.yaml")
    assert w.name == "headphones" and w.max_price == 25000 and w.open_listings == 5


def test_unknown_field_rejected(tmp_path):
    p = write(tmp_path, "watches:\n  - {name: phones, site: s, want: w, max_prize: 25000}\n")
    with pytest.raises(ConfigError) as e:
        load_watches(p)
    assert "phones" in str(e.value) and "max_prize" in str(e.value)
