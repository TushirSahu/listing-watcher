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
    """Matching listings with real links, one per link and per item_key (cheapest wins), cheapest first."""
    self_url = normalize_url(base) if base else None
    by_url: dict[str, Listing] = {}
    for item in items:
        if not item.matches:
            continue
        url = normalize_url(item.url, base)
        if not url.startswith(("http://", "https://")) or url == self_url:
            continue  # javascript:/# links (or the search page itself) aren't a listing
        by_url.setdefault(url, item.model_copy(update={"url": url}))
    by_key: dict[str, Listing] = {}
    out: list[Listing] = []
    for item in by_url.values():
        key = normalize_key(item.item_key)
        if key is None:
            out.append(item)
        elif key not in by_key or _cheaper(item, by_key[key]):
            by_key[key] = item
    out += by_key.values()
    return sorted(out, key=lambda i: (i.price is None, i.price or 0))
