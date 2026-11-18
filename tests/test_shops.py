import asyncio

from watcher.models import Listing
from watcher.shops import CHAINS, Plan, build_graph, current_chain, rank, run_shop_search


def L(url, price, grade=None, station=None, shop=None):
    return Listing(url=url, title=f"item {price}", price=price, why_matches="fits", matches=True, verified=True,
                   condition_grade=grade, branch_station=station, shop=shop)


def test_chains_have_search_urls_and_site_patterns():
    assert set(CHAINS) == {"hardoff", "bookoff", "trefac", "2ndstreet"}
    assert CHAINS["hardoff"].search_url("WH-1000XM5") == "https://netmall.hardoff.co.jp/search/?q=WH-1000XM5"
    assert CHAINS["bookoff"].search_url("ソニー ヘッドホン").startswith("https://shopping.bookoff.co.jp/search/keyword/")
    assert all(c.site.startswith("*.") for c in CHAINS.values())


def test_rank_ship_by_price_then_grade():
    plan = Plan(keywords="x", mode="ship")
    out = asyncio.run(rank(plan, [L("https://a.jp/1", 9000, "B"), L("https://a.jp/2", 8000, "C"), L("https://a.jp/3", 8000, "A"),
                      L("https://a.jp/4", None)], commute=None))
    assert [i.url for i in out] == ["https://a.jp/3", "https://a.jp/2", "https://a.jp/1", "https://a.jp/4"]


def test_rank_pickup_by_train_time():
    plan = Plan(keywords="x", mode="pickup", home_station="吹田")
    times = {"梅田": "15 min, direct, ¥230", "京都": "45 min, 1 transfer, ¥580"}
    asked = []

    async def commute(a, b):
        asked.append((a, b))
        return times.get(b, "no route found")

    items = [L("https://a.jp/kyoto", 5000, station="京都"), L("https://a.jp/umeda", 9000, station="梅田"),
             L("https://a.jp/umeda2", 7000, station="梅田"), L("https://a.jp/none", 1000)]
    out = asyncio.run(rank(plan, items, commute=commute))
    assert [i.url for i in out] == ["https://a.jp/umeda2", "https://a.jp/umeda", "https://a.jp/kyoto", "https://a.jp/none"]
    assert "15 min from 吹田" in out[0].why_matches
    assert sorted(asked) == [("吹田", "京都"), ("吹田", "梅田")]  # one lookup per branch station


def test_graph_runs_chains_in_parallel_and_merges():
    started, streamed = [], []
    plan = Plan(keywords="WH-1000XM5", max_price=25000, mode="ship", chains=["hardoff", "bookoff", "trefac"])

    async def plan_fn(text):
        return plan

    async def search_fn(chain, p, on_listing):
        started.append(chain)
        assert current_chain.get() == CHAINS[chain].name   # step lines can be tagged per chain
        await asyncio.sleep(0.05)
        item = L(f"https://{chain}.jp/1", {"hardoff": 9000, "bookoff": 7000, "trefac": 8000}[chain])
        on_listing(item)
        return [item, L("javascript:void(0)", 1)]  # junk link must be cleaned

    reply = asyncio.run(run_shop_search("find xm5", plan_fn=plan_fn, search_fn=search_fn, commute=None,
                                        on_listing=streamed.append))
    assert sorted(started) == ["bookoff", "hardoff", "trefac"]
    assert [i.price for i in reply.listings] == [7000, 8000, 9000]
    assert {i.shop for i in reply.listings} == {"BOOKOFF", "Hard Off", "Treasure Factory"}
    assert len(streamed) == 3
    assert "3 shops" in reply.answer and "price" in reply.answer


def test_graph_survives_one_chain_failing():
    async def plan_fn(text):
        return Plan(keywords="x", chains=["hardoff", "2ndstreet"])

    async def search_fn(chain, p, on_listing):
        if chain == "2ndstreet":
            raise RuntimeError("blocked")
        return [L("https://h.jp/1", 5000)]

    reply = asyncio.run(run_shop_search("x", plan_fn=plan_fn, search_fn=search_fn, commute=None))
    assert len(reply.listings) == 1 and "2nd STREET" in reply.answer and "couldn't" in reply.answer


def test_not_a_shop_request_returns_none():
    async def plan_fn(text):
        return Plan(keywords="x", target="marketplaces")

    async def search_fn(*a):
        raise AssertionError("must not search")

    assert asyncio.run(run_shop_search("x", plan_fn=plan_fn, search_fn=search_fn, commute=None)) is None


def test_build_graph_compiles():
    g = build_graph(plan_fn=None, search_fn=None, commute=None)
    assert {"plan", "search", "merge", "rank"} <= set(g.get_graph().nodes)


# --- real planner / per-chain search wiring (no network: pydantic-ai TestModel, fake ChatSession) ---
from pydantic_ai.models.test import TestModel

import watcher.shops as shops_mod
from watcher.models import Reply


class FakeLLM:
    def __init__(self, plan_args):
        self.plan_args = plan_args

    def pydantic_model(self):
        return TestModel(custom_output_args=self.plan_args, call_tools=[])


def test_make_shop_search_plans_and_searches(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self, llm, **kw):
            self.kw, self.closed, self.asked = kw, 0, []
            sessions.append(self)

        async def ask(self, text):
            self.asked.append(text)
            item = L(f"https://{self.kw['allowed_sites'][0][2:]}/item/1", 5000, "A", "梅田")
            self.kw["on_listing"](item)
            return Reply(answer="ok", listings=[item])

        async def close(self):
            self.closed += 1

    monkeypatch.setattr(shops_mod, "ChatSession", FakeSession)

    async def commute(a, b):
        return "12 min, direct, ¥200"

    monkeypatch.setattr(shops_mod, "lookup_commute", commute)
    llm = FakeLLM({"target": "shops", "keywords": "WH-1000XM5", "max_price": 25000, "mode": "pickup",
                   "home_station": "吹田", "chains": ["hardoff", "bookoff"]})
    search = shops_mod.make_shop_search(llm, {"headless": True})
    streamed = []
    reply = asyncio.run(search("xm5 near 吹田", streamed.append))
    assert {s.kw["allowed_sites"][0] for s in sessions} == {"*.hardoff.co.jp", "*.bookoff.co.jp"}
    hard = next(s for s in sessions if s.kw["allowed_sites"] == ["*.hardoff.co.jp"])
    task = hard.asked[0]
    assert "https://netmall.hardoff.co.jp/search/?q=WH-1000XM5" in task and "¥25,000" in task
    assert "branch_station" in task and "condition_grade" in task
    assert all(s.closed == 1 for s in sessions)
    assert len(reply.listings) == 2 and "12 min from 吹田" in reply.listings[0].why_matches
    assert len(streamed) == 2 and streamed[0].shop in {"Hard Off", "BOOKOFF"}


def test_make_shop_search_error_turn_counts_as_failed(monkeypatch):
    class ErrSession:
        def __init__(self, llm, **kw): pass
        async def ask(self, text): return Reply(answer="error: blocked")
        async def close(self): pass

    monkeypatch.setattr(shops_mod, "ChatSession", ErrSession)
    search = shops_mod.make_shop_search(FakeLLM({"keywords": "x", "chains": ["2ndstreet"]}), {})
    reply = asyncio.run(search("x", lambda i: None))
    assert reply.listings == [] and "couldn't search 2nd STREET" in reply.answer


def test_marketplace_request_returns_none(monkeypatch):
    search = shops_mod.make_shop_search(FakeLLM({"target": "marketplaces", "keywords": "x"}), {})
    assert asyncio.run(search("xm5 on mercari", lambda i: None)) is None
