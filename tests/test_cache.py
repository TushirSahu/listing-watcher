import asyncio
import sqlite3

import httpx

from watcher import cache
from watcher.models import Listing
from watcher.shop_pages import add_branches
from watcher.tools import lookup_commute

ROUTE = ('<span class="small">22分</span></li><li class="fare">370<!-- -->円</li>'
         '<li class="transfer">乗換：<span class="mark">1</span>回</li>')


def test_put_get_round_trip_and_missing():
    assert cache.get("route", ["a", "b"]) is None
    cache.put("route", ["a", "b"], "22 min")
    cache.put("place", "https://x/1", ["店", "住所"])
    assert cache.get("route", ["a", "b"]) == "22 min" and cache.get("place", "https://x/1") == ["店", "住所"]
    assert cache.get("place", ["a", "b"]) is None  # kinds don't mix


def test_expired_entry_is_missing(tmp_path):
    cache.put("route", ["a", "b"], "22 min")
    with sqlite3.connect(tmp_path / "cache.db") as db:
        db.execute("UPDATE cache SET saved = saved - ?", (cache.MAX_AGE + 1,))
    assert cache.get("route", ["a", "b"]) is None


def test_broken_cache_file_never_breaks_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DB", str(tmp_path))  # a folder, not a database
    cache.put("route", ["a", "b"], "x")
    assert cache.get("route", ["a", "b"]) is None


def test_commute_route_survives_restart():
    calls = []

    def handler(req):
        calls.append(req.url.params["to"])
        return httpx.Response(200, text=ROUTE)

    assert asyncio.run(lookup_commute("吹田", "江坂", transport=httpx.MockTransport(handler))) == "22 min, 1 transfer, ¥370"
    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert asyncio.run(lookup_commute("吹田", "江坂", transport=down)) == "22 min, 1 transfer, ¥370"  # read from disk
    assert calls == ["江坂"]


def test_commute_failures_not_cached():
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))
    assert asyncio.run(lookup_commute("吹田", "nowhere", transport=t)) == "no route found"
    assert cache.get("route", ["吹田", "nowhere"]) is None


def test_branch_address_survives_restart_and_failures_not_cached():
    page = "https://www.treasure-f.com/shop/34/"
    item = Listing(url="https://ec.treasure-f.com/item/1", title="x", price=500, why_matches="", matches=True,
                   verified=False, shop="Treasure Factory", branch="北越谷店", branch_url=page)
    shop = '<p class="add_area"> 〒343-0032<br>埼玉県越谷市袋山1852-1 </p>'

    failing = httpx.MockTransport(lambda r: httpx.Response(500))
    assert asyncio.run(add_branches([item], transport=failing))[0].branch_station is None
    assert cache.get("place", page) is None

    asyncio.run(add_branches([item], transport=httpx.MockTransport(lambda r: httpx.Response(200, text=shop))))
    [out] = asyncio.run(add_branches([item], transport=failing))  # new call, site down: answered from disk
    assert out.branch_station == "埼玉県越谷市袋山1852-1" and out.branch == "北越谷店"
