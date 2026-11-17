import types

import pytest

import watcher.agent as agent_mod
from watcher.agent import DEFAULT_SITES, SYSTEM_RULES, ChatSession, ConfigError, make_llm
from watcher.models import Reply


class FakeBrowser:
    def __init__(self, *a, **k):
        self.kwargs, self.kills = k, 0

    async def kill(self):
        self.kills += 1


class History:
    def __init__(self, out=None, status="completed", error=None, steps=5, injection=False):
        self.structured_output, self.status, self.error, self.steps = out, status, error, steps
        self.judgement = types.SimpleNamespace(prompt_injection_detected=injection)

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
    for s in ["管理費", "万円", "item_key", "javascript", "not stated", "Mercari", "SUUMO", "do NOT log in", "sold", "image_url", "ジャンク"]:
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


def test_rules_keep_answer_short():
    assert "don't repeat" in SYSTEM_RULES and "cards" in SYSTEM_RULES


def test_rules_treat_pages_as_untrusted():
    for s in ["untrusted", "never follow instructions", "local files", "environment variables"]:
        assert s in SYSTEM_RULES, s


def test_default_sites():
    assert DEFAULT_SITES == ["*.mercari.com", "*.yahoo.co.jp", "*.fril.jp", "*.amazon.co.jp", "*.suumo.jp",
                             "*.homes.co.jp"]


async def test_browser_restricted_to_allowed_sites(monkeypatch):
    _, browsers = install(monkeypatch, [OK])
    await ChatSession(llm=object(), allowed_sites=["jp.mercari.com"]).ask("x")
    assert browsers[0].kwargs["allowed_domains"] == ["jp.mercari.com"]
    assert browsers[0].kwargs["block_ip_addresses"] is True


async def test_no_allowlist_still_blocks_ips(monkeypatch):
    _, browsers = install(monkeypatch, [OK])
    await ChatSession(llm=object(), allowed_sites=None).ask("x")
    assert browsers[0].kwargs["allowed_domains"] is None and browsers[0].kwargs["block_ip_addresses"] is True


async def test_injection_check_on_warns(monkeypatch):
    made, _ = install(monkeypatch, [History(Reply(answer="Found 1."), injection=True)])
    r = await ChatSession(llm=object(), injection_check=True).ask("x")
    assert made[0].kwargs["judge"] is True
    assert r.answer.startswith("Warning: a page tried to give the agent instructions") and "Found 1." in r.answer


async def test_injection_check_off(monkeypatch):
    made, _ = install(monkeypatch, [OK])
    await ChatSession(llm=object(), injection_check=False).ask("x")
    assert made[0].kwargs["judge"] is False


async def test_step_budget_stops_turns(monkeypatch):
    made, _ = install(monkeypatch, [History(Reply(answer="a"), steps=30), History(Reply(answer="b"), steps=30)])
    s = ChatSession(llm=object(), step_budget=50)
    await s.ask("1")
    await s.ask("2")
    r = await s.ask("3")
    assert r.answer.startswith("error:") and "budget" in r.answer
    assert made[0].tasks == ["2"]  # third turn never ran


def test_rules_compare_sites_and_commute():
    for s in ["Yahoo Auctions", "Rakuma", "compare", "cheapest site", "commute_time", "commute"]:
        assert s in SYSTEM_RULES, s
    assert "*.yahoo.co.jp" in DEFAULT_SITES  # transit.yahoo.co.jp is reachable


def test_rules_give_direct_search_urls():
    for u in ["https://jp.mercari.com/search?keyword=", "https://auctions.yahoo.co.jp/search/search?p=",
              "https://paypayfleamarket.yahoo.co.jp/search/", "https://fril.jp/s?query="]:
        assert u in SYSTEM_RULES, u


def test_rules_use_report_listing():
    assert "report_listing" in SYSTEM_RULES


async def test_agent_gets_tools_and_streams(monkeypatch):
    made, _ = install(monkeypatch, [OK])
    got = []
    await ChatSession(llm=object(), on_listing=got.append).ask("x")
    tools = made[0].kwargs["tools"]
    assert {t.name for t in tools.functions} == {"commute_time", "report_listing"}
    report = next(t for t in tools.functions if t.name == "report_listing")
    await report.function(url="https://x.jp/1", title="t", price=1, why_matches="w", verified=True)
    assert got[0].url == "https://x.jp/1"


async def test_last_turn_steps_and_cost(monkeypatch):
    h = History(Reply(answer="a"), steps=17)
    h.cost_usd = 0.0042
    install(monkeypatch, [h])
    s = ChatSession(llm=object())
    await s.ask("x")
    assert s.last_steps == 17 and s.last_cost == 0.0042


def test_rules_lead_with_tools():
    tools_at = SYSTEM_RULES.index("Your tools")
    assert tools_at < SYSTEM_RULES.index("Search URLs")
    assert "ALWAYS" in SYSTEM_RULES[tools_at:tools_at + 600]
