from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from watcher.models import Listing

_TRACKING = {"gclid", "fbclid", "ref", "afid", "source_location"}


def normalize_url(url: str, base: str | None = None) -> str:
    """Canonical link: absolute, lowercase host, sorted query, no tracking params or fragment."""
    url = url.strip()
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    params = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in _TRACKING
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(params), ""))


def normalize_key(key: str | None) -> str | None:
    """Case- and whitespace-insensitive item identity; empty -> None."""
    return " ".join((key or "").lower().split()) or None


def _cheaper(a: Listing, b: Listing) -> bool:
    return a.price is not None and (b.price is None or a.price < b.price)


def clean_listings(items: list[Listing], base: str | None = None) -> list[Listing]:
    """Matching listings with real links, one per link and per item_key (cheapest wins), in the agent's order:
    the agent refers to them as #1, #2…, so reordering would make its numbers point at the wrong card."""
    self_url = normalize_url(base) if base else None
    out: list[Listing] = []
    seen_urls: set[str] = set()
    key_pos: dict[str, int] = {}
    for item in items:
        if not item.matches:
            continue
        url = normalize_url(item.url, base)
        if not url.startswith(("http://", "https://")) or url == self_url or url in seen_urls:
            continue  # javascript:/# links (or the search page itself) aren't a listing
        seen_urls.add(url)
        item = item.model_copy(update={"url": url})
        key = normalize_key(item.item_key)
        if key in key_pos:
            if _cheaper(item, out[key_pos[key]]):
                out[key_pos[key]] = item
            continue
        if key:
            key_pos[key] = len(out)
        out.append(item)
    return out
