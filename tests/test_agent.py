from datetime import timedelta

import pytest

import watcher.agent as agent_mod
from watcher.agent import build_task, make_llm, max_steps, run_watch
from watcher.config import ConfigError, Watch

W = Watch(name="headphones", site="https://jp.mercari.com", want="Sony WH-1000XM5, good condition",
          max_price=25000, every=timedelta(hours=2))


def test_make_llm_unknown_provider():
    with pytest.raises(ConfigError):
        make_llm("acme", "m", {"ACME_API_KEY": "x"})


def test_make_llm_empty_model():
    with pytest.raises(ConfigError):
        make_llm("anthropic", "", {"ANTHROPIC_API_KEY": "x"})


def test_make_llm_missing_key():
    with pytest.raises(ConfigError) as e:
        make_llm("anthropic", "m", {})
    assert "ANTHROPIC_API_KEY" in str(e.value)


def test_make_llm_ollama_no_key():
    assert make_llm("ollama", "qwen3", {}) is not None


def test_make_llm_anthropic_ok():
    assert make_llm("anthropic", "some-model", {"ANTHROPIC_API_KEY": "k"}) is not None


def test_build_task_contents():
    t = build_task(W)
    for s in ["https://jp.mercari.com", "Sony WH-1000XM5, good condition", "¥25,000",
              "read at most 10 results", "open the top 5 candidate listings",
              "do NOT log in, buy, message, or click purchase buttons", "blocked_or_lost"]:
        assert s in t, s


def test_build_task_zero_open():
    w = Watch(name="a", site="s", want="w", max_price=None, every=timedelta(hours=2), open_listings=0)
    t = build_task(w)
    assert "do not open listing pages" in t and "verified to false" in t
    assert "¥" not in t


def test_max_steps():
    assert max_steps(W) == 35


async def test_run_watch_never_raises(monkeypatch):
    class BoomAgent:
        def __init__(self, *a, **k): pass
        async def run(self, *a, **k): raise RuntimeError("boom")

    monkeypatch.setattr(agent_mod, "Agent", BoomAgent)
    monkeypatch.setattr(agent_mod, "Browser", lambda *a, **k: None)
    r = await run_watch(W, llm=object())
    assert r.failed and "boom" in r.note


async def test_run_watch_no_structured_output(monkeypatch):
    class History:
        structured_output = None
        def final_result(self): return "gave up"

    class NoOutAgent:
        def __init__(self, *a, **k): pass
        async def run(self, *a, **k): return History()

    monkeypatch.setattr(agent_mod, "Agent", NoOutAgent)
    monkeypatch.setattr(agent_mod, "Browser", lambda *a, **k: None)
    r = await run_watch(W, llm=object())
    assert r.failed and "gave up" in r.note


async def test_run_watch_failed_history_keeps_reason(monkeypatch):
    class History:
        status = "failed"
        error = "invalid x-api-key"
        structured_output = None
        def final_result(self): return None

    class FailAgent:
        def __init__(self, *a, **k): pass
        async def run(self, *a, **k): return History()

    monkeypatch.setattr(agent_mod, "Agent", FailAgent)
    monkeypatch.setattr(agent_mod, "Browser", lambda *a, **k: None)
    r = await run_watch(W, llm=object())
    assert r.failed and "invalid x-api-key" in r.note and "failed" in r.note


def test_build_task_skips_sold():
    t = build_task(W)
    assert "sold" in t.lower() and "売り切れ" in t


def test_build_task_price_and_key_rules():
    t = build_task(W)
    for s in ["管理費", "shipping", "敷金", "item_key"]:
        assert s in t, s


async def test_run_watch_passes_context_cap(monkeypatch):
    seen = {}

    class CapAgent:
        def __init__(self, *a, **k): seen.update(k)
        async def run(self, *a, **k): raise RuntimeError("stop")

    monkeypatch.setattr(agent_mod, "Agent", CapAgent)
    monkeypatch.setattr(agent_mod, "Browser", lambda *a, **k: None)
    await run_watch(W, llm=object(), max_context_chars=60000)
    assert seen["max_context_chars"] == 60000
    await run_watch(W, llm=object())
    assert seen["max_context_chars"] is None
