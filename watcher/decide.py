from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from watcher.config import Watch
from watcher.models import Listing, RunResult
from watcher.store import Store

_TRACKING = {"gclid", "fbclid", "ref", "afid", "source_location"}


def normalize_url(url: str, base: str | None = None) -> str:
    """Canonical key for a listing: absolute, lowercase host, sorted query, no tracking params or fragment."""
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


def _candidates(watch: Watch, result: RunResult) -> dict[str, Listing]:
    """Matching, in-budget listings keyed by normalized link; one per item_key (cheapest wins)."""
    by_url: dict[str, Listing] = {}
    for item in result.listings:
        if not item.matches:
            continue
        if watch.max_price is not None and item.price is not None and item.price > watch.max_price:
            continue
        by_url.setdefault(normalize_url(item.url, watch.site), item)
    out: dict[str, Listing] = {}
    key_url: dict[str, str] = {}
    for url, item in by_url.items():
        key = normalize_key(item.item_key)
        if key and key in key_url:
            if _cheaper(item, out[key_url[key]]):
                del out[key_url[key]]
                out[url], key_url[key] = item, url
            continue
        out[url] = item
        if key:
            key_url[key] = url
    return out


def process(watch: Watch, result: RunResult, store: Store,
            send: Callable[[str], bool] | None = None) -> list[str]:
    """Decide alerts for one run, deliver each via `send`, update the store. Returns the messages produced.

    A listing is recorded as seen only after its alert is delivered (send returned True), so a failed
    delivery is retried on the next run. Without `send`, every message counts as delivered.
    """
    msgs: list[str] = []

    def emit(text: str) -> bool:
        msgs.append(text)
        return send(text) if send else True

    name = watch.name
    if result.failed:
        if store.last_status(name) != "failed":
            emit(f"[{name}] check failed: {result.note}")
        store.set_status(name, "failed")
        return msgs

    if store.last_status(name) == "failed":
        emit(f"[{name}] working again")
    found = _candidates(watch, result)

    if not store.ever_succeeded(name):
        for url, item in found.items():
            store.record(name, url, item.price, normalize_key(item.item_key))
        summary = f"[{name}] now watching - {len(found)} current matches"
        priced = [(item.price, url) for url, item in found.items() if item.price is not None]
        if priced:
            price, url = min(priced)
            summary += f", cheapest ¥{price:,}: {url}"
        emit(summary)
    else:
        for url, item in found.items():
            key = normalize_key(item.item_key)
            url_seen = store.seen(name, url)
            if not url_seen and not (key and store.seen_key(name, key)):
                price = "price ?" if item.price is None else f"¥{item.price:,}"
                tag = "" if item.verified else " (unverified)"
                if emit(f"[{name}] new: {item.title} - {price}{tag}\n{item.why_matches}\n{url}"):
                    store.record(name, url, item.price, key)
                continue
            # same link, or same item under another link: alert only if it got cheaper
            old = store.last_price(name, url) if url_seen else store.key_price(name, key)
            if item.price is not None and old is not None and item.price < old:
                if emit(f"[{name}] price drop: {item.title} ¥{old:,} -> ¥{item.price:,}\n{url}"):
                    store.record(name, url, item.price, key)

    store.set_status(name, "ok")
    return msgs
