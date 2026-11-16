from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from watcher.config import Watch
from watcher.models import Listing, RunResult
from watcher.store import Store


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")])
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), query, ""))


def _yen(price: int | None) -> str:
    return "price ?" if price is None else f"¥{price:,}"


def _candidates(watch: Watch, result: RunResult) -> dict[str, Listing]:
    out: dict[str, Listing] = {}
    for item in result.listings:
        if not item.matches:
            continue
        if watch.max_price is not None and item.price is not None and item.price > watch.max_price:
            continue
        out.setdefault(normalize_url(item.url), item)
    return out


def process(watch: Watch, result: RunResult, store: Store) -> list[str]:
    name = watch.name
    if result.failed:
        msgs = [] if store.last_status(name) == "failed" else [f"[{name}] check failed: {result.note}"]
        store.set_status(name, "failed")
        return msgs

    msgs = [f"[{name}] working again"] if store.last_status(name) == "failed" else []
    found = _candidates(watch, result)

    if not store.ever_succeeded(name):
        for url, item in found.items():
            store.record(name, url, item.price)
        summary = f"[{name}] now watching - {len(found)} current matches"
        priced = [(item.price, url) for url, item in found.items() if item.price is not None]
        if priced:
            price, url = min(priced)
            summary += f", cheapest ¥{price:,}: {url}"
        msgs.append(summary)
    else:
        for url, item in found.items():
            if not store.seen(name, url):
                tag = "" if item.verified else " (unverified)"
                msgs.append(f"[{name}] new: {item.title} - {_yen(item.price)}{tag}\n{item.why_matches}\n{url}")
                store.record(name, url, item.price)
                continue
            old = store.last_price(name, url)
            if item.price is not None and old is not None and item.price < old:
                msgs.append(f"[{name}] price drop: {item.title} ¥{old:,} -> ¥{item.price:,}\n{url}")
                store.record(name, url, item.price)

    store.set_status(name, "ok")
    return msgs
