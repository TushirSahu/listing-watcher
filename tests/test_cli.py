from datetime import datetime, timedelta

import watcher.cli as cli
from watcher.cli import due, main, run_due
from watcher.config import Watch
from watcher.models import RunResult
from watcher.store import Store

NOW = datetime(2026, 11, 16, 12, 0)
GOOD_YAML = "watches:\n  - {name: a, site: https://x.jp, want: thing}\n"


def W(name):
    return Watch(name=name, site="s", want="w", max_price=None, every=timedelta(hours=2))


def test_due():
    s = Store(":memory:")
    a = W("a")
    assert due([a], s, NOW) == [a]
    s.set_last_run("a", NOW - timedelta(hours=1))
    assert due([a], s, NOW) == []
    s.set_last_run("a", NOW - timedelta(hours=2))
    assert due([a], s, NOW) == [a]


async def test_run_due_isolates_failures(monkeypatch):
    async def fake_run(watch, llm, headless=True):
        if watch.name == "a":
            raise RuntimeError("crash")
        return RunResult(searched=True, listings_seen=0, blocked_or_lost=False, note="", listings=[])

    monkeypatch.setattr(cli, "run_watch", fake_run)
    s = Store(":memory:")
    sent = []
    await run_due([W("a"), W("b")], s, object(), NOW, True, lambda t: sent.append(t) or True)
    assert len(sent) == 2
    assert "check failed" in sent[0] and "crash" in sent[0]
    assert s.last_run("a") == NOW and s.last_run("b") == NOW


async def test_run_due_failed_send_retries_listing(monkeypatch):
    from watcher.models import Listing

    item = Listing(url="https://x.jp/1", title="Sony", price=1, why_matches="fits", matches=True, verified=True)

    async def fake_run(watch, llm, headless=True):
        return RunResult(searched=True, listings_seen=1, blocked_or_lost=False, note="", listings=[item])

    monkeypatch.setattr(cli, "run_watch", fake_run)
    s = Store(":memory:")
    s.set_status("a", "ok")  # past first run
    await run_due([W("a")], s, object(), NOW, True, lambda t: False)
    assert not s.seen("a", "https://x.jp/1")


def _env(monkeypatch, tmp_path, **env):
    monkeypatch.chdir(tmp_path)
    for k in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_main_bad_config_exit_2(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    (tmp_path / "w.yaml").write_text("watches:\n  - {name: a, site: s}\n")
    assert main(["run", "--config", str(tmp_path / "w.yaml")]) == 2
    assert "config error" in capsys.readouterr().err


def test_main_missing_key_exit_2(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m")
    (tmp_path / "w.yaml").write_text(GOOD_YAML)
    assert main(["run", "--config", str(tmp_path / "w.yaml")]) == 2
    err = capsys.readouterr().err
    assert "config error" in err and "ANTHROPIC_API_KEY" in err


def test_check_unknown_name(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    (tmp_path / "w.yaml").write_text(GOOD_YAML)
    assert main(["check", "nope", "--config", str(tmp_path / "w.yaml")]) == 2
    assert "nope" in capsys.readouterr().err


def test_check_prints_and_does_not_store(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    (tmp_path / "w.yaml").write_text(GOOD_YAML)

    async def fake_run(watch, llm, headless=True):
        return RunResult.model_validate({"searched": True, "listings_seen": 1, "blocked_or_lost": False, "note": "",
                                         "listings": [{"url": "https://x.jp/1", "title": "Sony", "price": 100,
                                                       "why_matches": "fits", "matches": True, "verified": True}]})

    monkeypatch.setattr(cli, "run_watch", fake_run)
    assert main(["check", "a", "--config", str(tmp_path / "w.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Sony" in out and "https://x.jp/1" in out
    assert not list(tmp_path.glob("*.db"))
