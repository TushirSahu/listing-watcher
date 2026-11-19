"""Real tools the agent can call. They run in this process (not the agent's code sandbox), take typed
arguments, return short text for the model, never raise, and never fetch a URL the model chose."""
import asyncio
import re
from collections.abc import Callable

import httpx
from browser_use import Tools

from watcher.listings import clean_listings
from watcher.models import Listing

TRANSIT_URL = "https://transit.yahoo.co.jp/search/result"
_ROUTE = re.compile(
    r'<span class="small">(?:(\d+)時間)?(\d+)分</span></li>\s*<li class="fare">(?:<span class="mark">)?([\d,]+)<!-- -->円(?:</span>)?</li>'
    r'\s*<li class="transfer">乗換：(?:<!-- -->|<span class="mark">)*(\d+)'
)


def parse_commute(html: str) -> str:
    """Fastest route on a Yahoo!乗換案内 result page, e.g. '37 min, 1 transfer, ¥440'."""
    routes = [(int(h or 0) * 60 + int(m), fare, int(t)) for h, m, fare, t in _ROUTE.findall(html)]
    if not routes:
        return "no route found"
    minutes, fare, transfers = min(routes)
    change = "direct" if transfers == 0 else f"{transfers} transfer{'s' if transfers > 1 else ''}"
    return f"{minutes} min, {change}, ¥{fare}"


_ROUTES: dict[tuple[str, str], str] = {}  # (from, to) -> found route; note: per process, never expires
_GENTLE = asyncio.Semaphore(2)  # the site stops answering when hit with bursts


async def lookup_commute(from_station: str, to_station: str,
                       transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Train time between two places in Japan (station names or street addresses). Fixed host; the places are only
    query parameters. Found routes are cached; failures are not, so they are retried next time."""
    if (from_station, to_station) in _ROUTES:
        return _ROUTES[from_station, to_station]
    try:
        async with _GENTLE, httpx.AsyncClient(timeout=10, transport=transport,
                                              headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = await client.get(TRANSIT_URL, params={"from": from_station, "to": to_station,
                                                           "shin": "1", "ex": "1"})  # allow shinkansen + limited express
        route = parse_commute(resp.text)
    except Exception:
        return "no route found (lookup failed)"
    if route != "no route found":
        _ROUTES[from_station, to_station] = route
    return route


def make_tools(on_listing: Callable[[Listing], int | None] | None = None) -> Tools:
    """The agent's tool set. on_listing receives each confirmed listing as it is found (for streaming)."""
    tools = Tools()

    @tools.action("Train time between two stations in Japan, e.g. commute_time('吹田', '石橋阪大前'). "
                  "Use for rentals when the user names a place they commute to.")
    async def commute_time(from_station: str, to_station: str) -> str:
        return await lookup_commute(from_station, to_station)

    @tools.action("Show one confirmed matching listing to the user right away, before your final answer. "
                  "Call it for each listing you would include in your results.")
    async def report_listing(url: str, title: str, price: int | None, why_matches: str, verified: bool,
                             item_key: str | None = None, image_url: str | None = None, branch: str | None = None,
                             branch_station: str | None = None, condition_grade: str | None = None) -> str:
        item = Listing(url=url, title=title, price=price, why_matches=why_matches, matches=True,
                       verified=verified, item_key=item_key, image_url=image_url, branch=branch,
                       branch_station=branch_station, condition_grade=condition_grade)
        cleaned = clean_listings([item])
        if not cleaned:
            return ("not shown: url must be a full https link to the listing's own page, "
                    "e.g. https://jp.mercari.com/item/m123 (not /item/m123 or javascript:). Call again with the full link.")
        n = on_listing(cleaned[0]) if on_listing else None
        return f"shown as #{n}" if n else "shown"

    return tools
