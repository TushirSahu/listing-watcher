"""Read shop search result pages with plain HTTP and regex: no browser, no AI, no API key.

Each parser takes the result page HTML and returns in-stock listings in page order. Page layouts change;
when a parser returns nothing for a query that should match, compare the page with the test snippets."""
import asyncio
import re
from html import unescape
from urllib.parse import urljoin

import httpx

from watcher.models import Listing
from watcher.shops import CHAINS

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def _text(s: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _find(pattern: str, block: str) -> str | None:
    m = re.search(pattern, block, re.S)
    return m.group(1) if m else None


def _yen(s: str | None) -> int | None:
    return int(s.replace(",", "")) if s else None


def _page(pattern: str, ident: str | None) -> str | None:
    return pattern.format(ident) if ident else None


def _item(base: str, url, title, price, image=None, grade=None, branch=None, branch_url=None) -> Listing | None:
    if not url or not title:
        return None
    return Listing(url=urljoin(base, unescape(url)), title=_text(title), price=_yen(price), why_matches="",
                   matches=True, verified=False, image_url=urljoin(base, image) if image else None,
                   condition_grade=grade.upper() if grade else None, branch=_text(branch) if branch else None,
                   branch_url=branch_url)


ITEM_MARK = {"hardoff": '<div class="itemcolmn_item', "bookoff": 'class="productItem__inner"',
             "trefac": 'class="pj-search_item"'}  # start of one result block on each shop's page


def parse_hardoff(html: str) -> list[Listing]:
    out = []
    for b in html.split(ITEM_MARK["hardoff"])[1:]:
        title = " ".join(_text(t) for t in re.findall(r'class="item-(?:brand-name|name|code)">(.*?)</div>', b, re.S))
        out.append(_item("https://netmall.hardoff.co.jp/", _find(r'<a href="([^"]+)"', b), title,
                         _find(r'item-price-en">\s*([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
                         _find(r'icon-rank-[^"]*"\s+alt="([^"]+)"', b)))
    return [i for i in out if i]


def parse_bookoff(html: str) -> list[Listing]:
    out = []
    for b in html.split(ITEM_MARK["bookoff"])[1:]:
        if "在庫あり" not in b:
            continue
        out.append(_item("https://shopping.bookoff.co.jp/", _find(r'href="(/used/[^"]+)"', b),
                         _find(r'productItem__title">(.*?)</p>', b), _find(r'&yen;\s*([\d,]+)', b),
                         _find(r'<img[^>]*?\ssrc="([^"]+)"', b)))
    return [i for i in out if i]


def parse_trefac(html: str) -> list[Listing]:
    out = []
    for b in html.split(ITEM_MARK["trefac"])[1:]:
        if "cm-itemlist_soldout" in b:
            continue
        out.append(_item("https://ec.treasure-f.com/", _find(r'href="(/item/\d+)"', b), _find(r'alt="([^"]+)"', b),
                         _find(r'cm-itemlist_price[^>]*>\s*￥([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
                         branch=_find(r'cm-itemlist_shop_link.*?<span>(.*?)</span>', b),
                         branch_url=_page("https://www.treasure-f.com/shop/{}/",
                                          _find(r'cm-itemlist_shop_link.*?shop=(\d+)', b))))
    return [i for i in out if i]


PARSERS = {"hardoff": parse_hardoff, "bookoff": parse_bookoff, "trefac": parse_trefac}  # 2nd STREET: 403 to plain HTTP
NO_RESULTS = {"hardoff": "該当する商品が見つかりませんでした", "bookoff": "該当する商品が見つかりませんでした",
              "trefac": "検索結果（0件）"}  # text each shop shows when nothing matches


class LayoutChanged(Exception):
    """The page loaded but neither listings nor the shop's 'no results' text were found: the parser needs updating."""


async def fetch_shop(key: str, keywords: str, max_price: int | None = None,
                     transport: httpx.AsyncBaseTransport | None = None) -> list[Listing]:
    """One chain's in-stock results within budget. Raises on HTTP errors and LayoutChanged (caller reports the shop
    as failed). An honest 'no results' page (Hard Off sends it with status 404) returns []."""
    chain = CHAINS[key]
    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        resp = await client.get(chain.search_url(keywords))
    empty = NO_RESULTS[key] in resp.text
    if empty and resp.status_code in (200, 404):
        return []
    resp.raise_for_status()
    items = [i.model_copy(update={"shop": chain.name}) for i in PARSERS[key](resp.text)]
    if not items and ITEM_MARK[key] not in resp.text:  # blocks present but none kept (e.g. all sold out) is fine
        raise LayoutChanged(chain.name)
    return [i for i in items if max_price is None or (i.price is not None and i.price <= max_price)]


# --- pick-up: which branch holds an item, and its street address (the train-time site accepts addresses) ---
def _address(raw: str | None) -> str | None:
    """'〒980-0871宮城県仙台市青葉区八幡3-1-50 レキシントンプラザ八幡2F' -> '宮城県仙台市青葉区八幡3-1-50' (building dropped)."""
    if not raw:
        return None
    return _text(re.sub(r"〒?\d{3}-\d{4}", " ", _text(raw))).split(" ")[0] or None


def parse_hardoff_item(html: str) -> tuple[str | None, str | None]:
    """Hard Off item page -> (branch name, address). Search results don't say which branch has the item."""
    name = _find(r'"seller":\{[^}]*"name":"([^"]+)"', html)
    return (unescape(name) if name else None,
            _address(_find(r'product-detail-store__address">(.*?)</p>', html)))


def parse_trefac_shop(html: str) -> str | None:
    """Treasure Factory branch page -> address."""
    return _address(_find(r'class="add_area">(.*?)</p>', html))


_PLACES: dict[str, tuple[str | None, str | None]] = {}  # page url -> (branch, address)
# note: per-process cache, lost on restart; persist to SQLite if repeat sessions feel slow


async def add_branches(items: list[Listing], limit: int = 10,
                       transport: httpx.AsyncBaseTransport | None = None) -> list[Listing]:
    """Pick-up mode: fill branch and branch_station (its address) for each shop's `limit` cheapest items,
    from Hard Off's item page or Treasure Factory's branch page. Pages that fail are skipped; order is kept."""
    def page(i: Listing) -> str | None:
        return {"Hard Off": i.url, "Treasure Factory": i.branch_url}.get(i.shop or "")

    todo: list[str] = []
    for shop in ("Hard Off", "Treasure Factory"):
        cheapest = sorted((i for i in items if i.shop == shop and not i.branch_station and page(i)),
                          key=lambda i: (i.price is None, i.price or 0))
        todo += list(dict.fromkeys(page(i) for i in cheapest))[:limit]
    limit_host = asyncio.Semaphore(3)

    async def fetch(client: httpx.AsyncClient, url: str):
        if url in _PLACES:
            return
        async with limit_host:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                return  # not cached: try again next search
        _PLACES[url] = parse_hardoff_item(resp.text) if "hardoff" in url else (None, parse_trefac_shop(resp.text))

    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        await asyncio.gather(*(fetch(client, u) for u in todo))

    def fill(i: Listing) -> Listing:
        name, address = _PLACES.get(page(i) or "", (None, None))
        if not address:
            return i
        return i.model_copy(update={"branch": name or i.branch, "branch_station": address})

    return [fill(i) for i in items]
