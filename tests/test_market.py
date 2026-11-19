import json

import httpx
import pytest

from watcher import market
from watcher.shop_pages import LayoutChanged


def page(items):
    data = {"props": {"pageProps": {"initialState": {"search": {"items": {"listing": {"items": items}}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def sold(price, cond="USED20", title="SONY WH-1000XM5", end="2026-11-18T22:00:00+09:00"):
    return {"auctionId": f"x{price}", "title": title, "price": price, "itemCondition": cond, "bidCount": 3, "endTime": end}


SALES = [sold(15000), sold(16000), sold(17000), sold(18000), sold(19000, end="2026-10-20T10:00:00+09:00"),
         sold(39000, "NEW"), sold(3000, "USED80"), sold(5000, title="WH-1000XM5 ジャンク 音出ず"),
         sold(2500, title="WH-1000XM5 ケースのみ"), sold(16500, title="WH-1000XM5 本体のみ")]


async def test_market_price_median_of_used_sales_only():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=page(SALES))

    m = await market.market_price("WH-1000XM5", transport=httpx.MockTransport(handler))
    assert m["median"] == 16750 and m["count"] == 6          # new, poor-condition, junk, case-only out; unit-only kept
    assert m["low"] <= 16000 and m["high"] >= 18000
    assert m["since"] == "2026-10-20" and "auctions.yahoo.co.jp/closedsearch" in seen[0]
    again = await market.market_price("WH-1000XM5", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert again == m                                         # cached: no second request needed


async def test_market_price_none_when_too_few_sales():
    t = httpx.MockTransport(lambda r: httpx.Response(200, text=page([sold(15000), sold(16000)])))
    assert await market.market_price("rare thing", transport=t) is None


async def test_market_price_layout_change_raises():
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>no data</html>"))
    with pytest.raises(LayoutChanged):
        await market.market_price("x", transport=t)


def test_vs_market():
    assert market.vs_market(19800, 17000) == 16 and market.vs_market(None, 17000) is None
