"""What a model actually sells for: recent Yahoo! Auctions closed listings (落札相場), read from the page's embedded
JSON. Used-condition sales only; new, poor-condition, junk and accessory-only listings are left out."""
import json
import re
import statistics
from urllib.parse import quote

import httpx

from watcher import cache
from watcher.shop_pages import UA, LayoutChanged

URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p={q}&n=50"
MIN_SALES = 3
DAY = 24 * 3600
_SKIP_CONDITION = {"NEW", "USED80"}  # new / 全体的に状態が悪い
_SKIP_TITLE = re.compile(r"ジャンク|部品取り|動作未確認|(ケース|箱|外箱|イヤーパッド|リモコン)のみ")  # junk, parts, untested, accessory only


def parse_sold(html: str) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    try:
        return json.loads(m.group(1))["props"]["pageProps"]["initialState"]["search"]["items"]["listing"]["items"]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise LayoutChanged("Yahoo! Auctions") from None


async def market_price(keywords: str, transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    """Median and middle half of recent used sales, or None when fewer than MIN_SALES. Cached for a day."""
    if cached := cache.get("market", keywords, max_age=DAY):
        return cached
    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        resp = await client.get(URL.format(q=quote(keywords)))
    resp.raise_for_status()
    sales = [s for s in parse_sold(resp.text)
             if s.get("price") and s.get("itemCondition") not in _SKIP_CONDITION and not _SKIP_TITLE.search(s["title"])]
    if len(sales) < MIN_SALES:
        return None
    prices = [s["price"] for s in sales]
    low, _, high = statistics.quantiles(prices, n=4)
    result = {"median": int(statistics.median(prices)), "low": int(low), "high": int(high), "count": len(prices),
              "since": min(s["endTime"][:10] for s in sales), "source": "Yahoo! Auctions recent used sales"}
    cache.put("market", keywords, result)
    return result


def vs_market(price: int | None, median: int) -> int | None:
    """Percent above (+) or below (-) the typical used price."""
    return round((price - median) * 100 / median) if price else None
