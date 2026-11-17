import pytest

import watcher.agent as agent_mod
from watcher.agent import SYSTEM_RULES, ChatSession, ConfigError, make_llm
from watcher.models import Reply


class FakeBrowser:
    def __init__(self, *a, **k):
        self.kwargs, self.kills = k, 0

    async def kill(self):
        self.kills += 1


class History:
    def __init__(self, out=None, status="completed", error=None):
        self.structured_output, self.status, self.error = out, status, error

    def final_result(self):
        return None


def install(monkeypatch, behaviours):
    """behaviours: list of History or Exception, consumed one per run()."""
    made = []

    class FakeAgent:
        def __init__(self, **k):
            self.kwargs, self.tasks = k, []
            made.append(self)

        def add_new_task(self, task):
            self.tasks.append(task)

        async def run(self, max_steps=100):
            b = behaviours.pop(0)
            if isinstance(b, Exception):
                raise b
            return b

    browsers = []
    monkeypatch.setattr(agent_mod, "Agent", FakeAgent)
    monkeypatch.setattr(agent_mod, "Browser", lambda *a, **k: browsers.append(FakeBrowser(*a, **k)) or browsers[-1])
    return made, browsers


OK = History(Reply(answer="found 1", listings=[]))


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


def test_google_retries_rate_limits():
    model = make_llm("google", "gemini-x", {"GOOGLE_API_KEY": "k"}).pydantic_model()
    opts = model._provider._client._api_client._http_options.retry_options
    assert opts.attempts >= 5 and 429 in opts.http_status_codes and 503 in opts.http_status_codes


def test_rules_cover_essentials():
    for s in ["管理費", "万円", "item_key", "javascript", "not stated", "Mercari", "SUUMO", "do NOT log in", "sold"]:
        assert s in SYSTEM_RULES, s


async def test_first_ask_creates_agent_followup_adds_task(monkeypatch):
    made, browsers = install(monkeypatch, [OK, OK])
    s = ChatSession(llm=object(), max_context_chars=60000)
    r = await s.ask("find headphones")
    await s.ask("only with case")
    assert r.answer == "found 1"
    assert len(made) == 1 and len(browsers) == 1
    k = made[0].kwargs
    assert k["task"] == "find headphones" and k["output_model_schema"] is Reply
    assert k["extend_system_message"] == SYSTEM_RULES and k["max_context_chars"] == 60000
    assert made[0].tasks == ["only with case"]
    assert browsers[0].kwargs["keep_alive"] is True


async def test_ask_error_returns_error_reply(monkeypatch):
    made, browsers = install(monkeypatch, [RuntimeError("boom")])
    s = ChatSession(llm=object())
    r = await s.ask("x")
    assert r.answer.startswith("error:") and "boom" in r.answer and r.listings == []
    assert browsers[0].kills == 0


async def test_followup_after_error_continues(monkeypatch):
    made, _ = install(monkeypatch, [RuntimeError("boom"), OK])
    s = ChatSession(llm=object())
    await s.ask("x")
    await s.ask("y")
    assert len(made) == 1 and made[0].tasks == ["y"]


async def test_no_structured_output(monkeypatch):
    install(monkeypatch, [History(None, status="failed", error="quota exceeded")])
    r = await ChatSession(llm=object()).ask("x")
    assert r.answer.startswith("error:") and "quota exceeded" in r.answer


async def test_close_kills_browser_once_and_resets(monkeypatch):
    made, browsers = install(monkeypatch, [OK, OK])
    s = ChatSession(llm=object())
    await s.ask("x")
    await s.close()
    await s.close()
    assert browsers[0].kills == 1
    await s.ask("fresh")
    assert len(made) == 2 and made[1].kwargs["task"] == "fresh"
