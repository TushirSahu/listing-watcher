import watcher.cli as cli
from watcher.cli import chat_loop, main, render
from watcher.models import Listing, Reply


def L(n, price, title):
    return Listing(url=f"https://x.jp/{n}", title=title, price=price, why_matches=f"why {n}", matches=True, verified=True)


def test_render_numbered():
    r = Reply(answer="Found 2.", listings=[L(1, 19666, "WH-1000XM5"), L(2, None, "XM5 case")])
    assert render(r) == (
        "Found 2.\n"
        "  1. ¥19,666  WH-1000XM5\n"
        "     why 1\n"
        "     https://x.jp/1\n"
        "  2. ¥?  XM5 case\n"
        "     why 2\n"
        "     https://x.jp/2"
    )


def test_render_cleans_listings():
    bad = Listing(url="javascript:void(0)", title="x", price=1, why_matches="", matches=True, verified=True)
    assert render(Reply(answer="hi", listings=[bad])) == "hi"


class FakeSession:
    def __init__(self):
        self.asked, self.closed = [], 0

    async def ask(self, text):
        self.asked.append(text)
        return Reply(answer=f"ok {text}")

    async def close(self):
        self.closed += 1


def reader(*lines, end=EOFError):
    it = iter(lines)

    async def read():
        try:
            return next(it)
        except StopIteration:
            raise end()
    return read


async def run(*lines, end=EOFError, shop_search=None):
    s, out = FakeSession(), []
    await chat_loop(s, reader(*lines, end=end), out.append, shop_search)
    return s, out


def fake_shop(reply):
    calls = []

    async def shop_search(text, on_listing):
        calls.append(text)
        if isinstance(reply, Exception):
            raise reply
        return reply
    return shop_search, calls


async def test_first_search_goes_to_shops():
    shop, calls = fake_shop(Reply(answer="shops found", listings=[L(1, 500, "bento")]))
    s, out = await run("cheap bento", shop_search=shop)
    assert calls == ["cheap bento"] and s.asked == []
    assert out == [render(Reply(answer="shops found", listings=[L(1, 500, "bento")]))]


async def test_shop_none_falls_back_to_agent():
    shop, calls = fake_shop(None)
    s, out = await run("mercari xm5", shop_search=shop)
    assert calls == ["mercari xm5"] and s.asked == ["mercari xm5"] and "ok mercari xm5" in out


async def test_shop_error_falls_back_to_agent():
    shop, _ = fake_shop(RuntimeError("boom"))
    s, out = await run("cheap bento", shop_search=shop)
    assert s.asked == ["cheap bento"] and "ok cheap bento" in out


async def test_follow_up_after_shops_gets_recap():
    shop, calls = fake_shop(Reply(answer="shops found", listings=[L(1, 500, "bento")]))
    s, _ = await run("cheap bento", "only under 400", "and nearby", shop_search=shop)
    assert calls == ["cheap bento"]
    first, second = s.asked
    assert "Earlier in this conversation" in first and "bento" in first and "https://x.jp/1" in first
    assert first.endswith("New message: only under 400")
    assert second == "and nearby"  # the agent saw the recap once and keeps its own context after


async def test_new_lets_next_search_use_shops():
    shop, calls = fake_shop(Reply(answer="shops found", listings=[L(1, 500, "bento")]))
    s, _ = await run("cheap bento", "/new", "cheap ramen", "spicier", shop_search=shop)
    assert calls == ["cheap bento", "cheap ramen"]
    assert len(s.asked) == 1 and "User: cheap ramen" in s.asked[0] and "cheap bento" not in s.asked[0]


async def test_loop_asks_and_prints():
    s, out = await run("find xm5", "only with case")
    assert s.asked == ["find xm5", "only with case"]
    assert "ok find xm5" in out and "ok only with case" in out


async def test_blank_ignored():
    s, _ = await run("", "   ", "x")
    assert s.asked == ["x"]


async def test_new_resets():
    s, out = await run("a", "/new", "b")
    assert s.asked == ["a", "b"] and s.closed >= 2  # /new closes, exit closes
    assert any("new search" in o for o in out)


async def test_quit_closes():
    s, _ = await run("a", "/quit", "never")
    assert s.asked == ["a"] and s.closed == 1


async def test_eof_closes():
    s, _ = await run("a")
    assert s.closed == 1


async def test_ctrl_c_closes():
    s, _ = await run("a", end=KeyboardInterrupt)
    assert s.closed == 1


def _env(monkeypatch, tmp_path, **env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HISTORY_DB", str(tmp_path / "history.db"))  # never touch the real ~/.thrift-scout
    for k in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "LLM_MAX_CONTEXT_CHARS", "ALLOWED_SITES",
              "INJECTION_CHECK", "MAX_STEPS_PER_RUN"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_main_missing_key_exit_2(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m")
    assert main(["chat"]) == 2
    err = capsys.readouterr().err
    assert "config error" in err and "ANTHROPIC_API_KEY" in err


def test_bad_context_cap_exit_2(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k",
         LLM_MAX_CONTEXT_CHARS="lots")
    assert main(["chat"]) == 2
    assert "LLM_MAX_CONTEXT_CHARS" in capsys.readouterr().err


def test_main_runs_loop_with_cap(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k",
         LLM_MAX_CONTEXT_CHARS="60000")
    seen = {}

    async def fake_loop(session, read, write, shop_search=None):
        seen["cap"], seen["headless"] = session.max_context_chars, session.headless
        seen["shop"] = shop_search is not None

    monkeypatch.setattr(cli, "chat_loop", fake_loop)
    assert main(["chat", "--show-browser"]) == 0
    assert seen == {"cap": 60000, "headless": False, "shop": True}


def test_web_serves_on_localhost(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port, **k: seen.update(host=host, port=port, app=app))
    assert main(["web", "--port", "8123", "--show-browser"]) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8123
    assert seen["app"].state.chat.session.headless is False


def _session_from(monkeypatch, tmp_path, **env):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k", **env)
    seen = {}

    async def fake_loop(session, read, write, shop_search=None):
        seen["s"] = session

    monkeypatch.setattr(cli, "chat_loop", fake_loop)
    assert main(["chat"]) == 0
    return seen["s"]


def test_security_defaults(monkeypatch, tmp_path):
    from watcher.agent import DEFAULT_SITES
    s = _session_from(monkeypatch, tmp_path)
    assert s.allowed_sites == DEFAULT_SITES and s.injection_check is True and s.step_budget == 600


def test_security_env_overrides(monkeypatch, tmp_path):
    from watcher.agent import DEFAULT_SITES
    s = _session_from(monkeypatch, tmp_path, ALLOWED_SITES="example.jp, *.shop.jp", INJECTION_CHECK="off",
                      MAX_STEPS_PER_RUN="100")
    assert s.allowed_sites == DEFAULT_SITES + ["example.jp", "*.shop.jp"]
    assert s.injection_check is False and s.step_budget == 100


def test_allowed_sites_star_disables(monkeypatch, tmp_path):
    assert _session_from(monkeypatch, tmp_path, ALLOWED_SITES="*").allowed_sites is None


def test_bad_step_budget_exit_2(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k", MAX_STEPS_PER_RUN="lots")
    assert main(["chat"]) == 2
    assert "MAX_STEPS_PER_RUN" in capsys.readouterr().err


def test_web_host_flag(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port, **k: seen.update(host=host))
    assert main(["web", "--host", "0.0.0.0"]) == 0
    assert seen["host"] == "0.0.0.0"


def test_web_uses_history_db(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="m", ANTHROPIC_API_KEY="k")
    monkeypatch.setenv("HISTORY_DB", str(tmp_path / "x" / "h.db"))
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port, **k: seen.update(app=app))
    assert main(["web"]) == 0
    assert (tmp_path / "x" / "h.db").exists()


def test_web_enables_shop_search(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, LLM_PROVIDER="google", LLM_MODEL="m", GOOGLE_API_KEY="k")
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port, **k: seen.update(app=app))
    assert main(["web"]) == 0
    assert seen["app"].state.chat.shop_search is not None
