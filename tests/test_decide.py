from datetime import timedelta

from watcher.config import Watch
from watcher.decide import normalize_url, process
from watcher.models import Listing, RunResult, failed_result
from watcher.store import Store

W = Watch(name="w", site="https://x.jp", want="thing", max_price=None, every=timedelta(hours=2))


def lst(url, price, matches=True, verified=True, title="Item"):
    return Listing(url=url, title=title, price=price, why_matches="fits", matches=matches, verified=verified)


def ok(*listings):
    return RunResult(searched=True, listings_seen=len(listings), blocked_or_lost=False, note="", listings=list(listings))


def started(store, *listings, watch=W):
    process(watch, ok(*listings), store)


def test_first_run_summary_only():
    s = Store(":memory:")
    msgs = process(W, ok(lst("https://x.jp/1", 300), lst("https://x.jp/2", 100), lst("https://x.jp/3", 200)), s)
    assert len(msgs) == 1
    assert msgs[0].startswith("[w] now watching - 3 current matches, cheapest ¥")
    assert "¥100: https://x.jp/2" in msgs[0]
    assert all(s.seen("w", f"https://x.jp/{i}") for i in (1, 2, 3))


def test_first_run_no_prices_summary():
    s = Store(":memory:")
    assert process(W, ok(lst("https://x.jp/1", None)), s) == ["[w] now watching - 1 current matches"]


def test_new_listing_alerts_once():
    s = Store(":memory:")
    started(s, lst("https://x.jp/1", 100))
    msgs = process(W, ok(lst("https://x.jp/1", 100), lst("https://x.jp/2", 25000, title="Sony")), s)
    assert msgs == ["[w] new: Sony - ¥25,000\nfits\nhttps://x.jp/2"]
    assert process(W, ok(lst("https://x.jp/2", 25000)), s) == []


def test_new_listing_no_price():
    s = Store(":memory:")
    started(s)
    [m] = process(W, ok(lst("https://x.jp/2", None, title="Sony")), s)
    assert m.startswith("[w] new: Sony - price ?")


def test_price_drop():
    s = Store(":memory:")
    started(s, lst("https://x.jp/1", 20000))
    [m] = process(W, ok(lst("https://x.jp/1", 18000, title="Sony")), s)
    assert m == "[w] price drop: Sony ¥20,000 -> ¥18,000\nhttps://x.jp/1"
    assert process(W, ok(lst("https://x.jp/1", 19000)), s) == []


def test_non_matching_ignored():
    s = Store(":memory:")
    started(s)
    assert process(W, ok(lst("https://x.jp/9", 100, matches=False)), s) == []
    assert not s.seen("w", "https://x.jp/9")


def test_over_max_price_ignored():
    s = Store(":memory:")
    w = Watch(name="w", site="s", want="t", max_price=25000, every=timedelta(hours=2))
    started(s, watch=w)
    assert process(w, ok(lst("https://x.jp/9", 30000)), s) == []
    assert not s.seen("w", "https://x.jp/9")


def test_url_variants_deduped():
    s = Store(":memory:")
    started(s, lst("https://x.jp/item/m1", 100))
    assert process(W, ok(lst("https://x.jp/item/m1?utm_source=a#p", 100)), s) == []
    assert normalize_url("https://x.jp/s?id=5&utm_x=1") == "https://x.jp/s?id=5"
    assert normalize_url("https://x.jp/item/m1/") == "https://x.jp/item/m1"


def test_duplicates_within_one_run():
    s = Store(":memory:")
    started(s)
    msgs = process(W, ok(lst("https://x.jp/2", 1), lst("https://x.jp/2#a", 1)), s)
    assert len(msgs) == 1


def test_failure_alerts_on_change_only():
    s = Store(":memory:")
    started(s)
    assert process(W, failed_result("captcha"), s) == ["[w] check failed: captcha"]
    assert process(W, failed_result("captcha"), s) == []
    assert process(W, ok(), s) == ["[w] working again"]


def test_failure_records_nothing():
    s = Store(":memory:")
    started(s)
    r = RunResult(searched=True, listings_seen=1, blocked_or_lost=True, note="lost", listings=[lst("https://x.jp/5", 1)])
    process(W, r, s)
    assert not s.seen("w", "https://x.jp/5")


def test_failure_before_first_success_then_summary():
    s = Store(":memory:")
    assert process(W, failed_result("boom"), s) == ["[w] check failed: boom"]
    msgs = process(W, ok(lst("https://x.jp/1", 5), lst("https://x.jp/2", 6)), s)
    assert msgs[0] == "[w] working again"
    assert msgs[1].startswith("[w] now watching - 2 current matches")
    assert len(msgs) == 2


def test_unverified_marked():
    s = Store(":memory:")
    started(s)
    [m] = process(W, ok(lst("https://x.jp/2", 100, verified=False, title="Sony")), s)
    assert m.splitlines()[0] == "[w] new: Sony - ¥100 (unverified)"


def test_url_normalization_variants():
    base = "https://jp.mercari.com"
    want = "https://jp.mercari.com/item/m1"
    assert normalize_url("/item/m1", base) == want
    assert normalize_url("https://JP.Mercari.com/item/m1", base) == want
    assert normalize_url("https://jp.mercari.com/item/m1?gclid=1&fbclid=2&ref=x&afid=9", base) == want
    assert normalize_url("https://x.jp/s?b=1&a=2") == normalize_url("https://x.jp/s?a=2&b=1")


def test_relative_url_dedupes_against_absolute():
    s = Store(":memory:")
    w = Watch(name="w", site="https://jp.mercari.com", want="t", max_price=None, every=timedelta(hours=2))
    started(s, lst("https://jp.mercari.com/item/m1", 100), watch=w)
    assert process(w, ok(lst("/item/m1", 100)), s) == []


def test_failed_send_not_recorded_retries_next_run():
    s = Store(":memory:")
    started(s)
    process(W, ok(lst("https://x.jp/2", 100)), s, send=lambda text: False)
    assert not s.seen("w", "https://x.jp/2")
    assert len(process(W, ok(lst("https://x.jp/2", 100)), s)) == 1


def klst(url, price, key):
    return Listing(url=url, title="Flat", price=price, why_matches="fits", matches=True, verified=True, item_key=key)


def test_no_key_unchanged():
    s = Store(":memory:")
    started(s, lst("https://x.jp/1", 100))
    assert len(process(W, ok(lst("https://x.jp/2", 100)), s)) == 1


def test_same_key_new_link_not_new():
    s = Store(":memory:")
    started(s, klst("https://x.jp/1", 60000, "Maison A 3F 1K"))
    assert process(W, ok(klst("https://x.jp/2", 60000, "Maison A 3F 1K")), s) == []


def test_key_normalized():
    s = Store(":memory:")
    started(s, klst("https://x.jp/1", 60000, "Maison A 3F 1K"))
    assert process(W, ok(klst("https://x.jp/2", 60000, " maison a  3f 1k")), s) == []


def test_same_key_cheaper_link_is_drop():
    s = Store(":memory:")
    started(s, klst("https://x.jp/1", 60000, "Maison A 3F 1K"))
    [m] = process(W, ok(klst("https://x.jp/2", 57000, "Maison A 3F 1K")), s)
    assert m.startswith("[w] price drop:") and "¥60,000 -> ¥57,000" in m and "https://x.jp/2" in m


def test_same_key_pricier_ignored():
    s = Store(":memory:")
    started(s, klst("https://x.jp/1", 60000, "Maison A 3F 1K"))
    assert process(W, ok(klst("https://x.jp/2", 63000, "Maison A 3F 1K")), s) == []
    assert s.key_price("w", "maison a 3f 1k") == 60000


def test_same_key_twice_in_run():
    s = Store(":memory:")
    started(s)
    [m] = process(W, ok(klst("https://x.jp/1", 61000, "B 2F"), klst("https://x.jp/2", 59000, "B 2F")), s)
    assert "¥59,000" in m and m.startswith("[w] new:")
