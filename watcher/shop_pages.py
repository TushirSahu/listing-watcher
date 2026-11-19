"""Read shop search result pages with plain HTTP and regex: no browser, no AI, no API key.

Each parser takes the result page HTML and returns in-stock listings in page order. Page layouts change;
when a parser returns nothing for a query that should match, compare the page with the test snippets."""
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


def _item(base: str, url, title, price, image=None, grade=None, branch=None) -> Listing | None:
    if not url or not title:
        return None
    return Listing(url=urljoin(base, unescape(url)), title=_text(title), price=_yen(price), why_matches="",
                   matches=True, verified=False, image_url=urljoin(base, image) if image else None,
                   condition_grade=grade.upper() if grade else None, branch=_text(branch) if branch else None)


def parse_hardoff(html: str) -> list[Listing]:
    out = []
    for b in html.split('<div class="itemcolmn_item')[1:]:
        title = " ".join(_text(t) for t in re.findall(r'class="item-(?:brand-name|name|code)">(.*?)</div>', b, re.S))
        out.append(_item("https://netmall.hardoff.co.jp/", _find(r'<a href="([^"]+)"', b), title,
                         _find(r'item-price-en">\s*([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
                         _find(r'icon-rank-[^"]*"\s+alt="([^"]+)"', b)))
    return [i for i in out if i]


def parse_bookoff(html: str) -> list[Listing]:
    out = []
    for b in html.split('class="productItem__inner"')[1:]:
        if "在庫あり" not in b:
            continue
        out.append(_item("https://shopping.bookoff.co.jp/", _find(r'href="(/used/[^"]+)"', b),
                         _find(r'productItem__title">(.*?)</p>', b), _find(r'&yen;\s*([\d,]+)', b),
                         _find(r'<img[^>]*?\ssrc="([^"]+)"', b)))
    return [i for i in out if i]


def parse_trefac(html: str) -> list[Listing]:
    out = []
    for b in html.split('class="pj-search_item"')[1:]:
        out.append(_item("https://ec.treasure-f.com/", _find(r'href="(/item/\d+)"', b), _find(r'alt="([^"]+)"', b),
                         _find(r'cm-itemlist_price[^>]*>\s*￥([\d,]+)', b), _find(r'<img[^>]*?\ssrc="([^"]+)"', b),
                         branch=_find(r'cm-itemlist_shop_link.*?<span>(.*?)</span>', b)))
    return [i for i in out if i]


PARSERS = {"hardoff": parse_hardoff, "bookoff": parse_bookoff, "trefac": parse_trefac}  # 2nd STREET: 403 to plain HTTP


async def fetch_shop(key: str, keywords: str, max_price: int | None = None,
                     transport: httpx.AsyncBaseTransport | None = None) -> list[Listing]:
    """One chain's in-stock results within budget. Raises on HTTP errors (caller reports the shop as failed)."""
    chain = CHAINS[key]
    async with httpx.AsyncClient(timeout=20, transport=transport, headers={"User-Agent": UA}) as client:
        resp = await client.get(chain.search_url(keywords))
    resp.raise_for_status()
    items = [i.model_copy(update={"shop": chain.name}) for i in PARSERS[key](resp.text)]
    return [i for i in items if max_price is None or (i.price is not None and i.price <= max_price)]
