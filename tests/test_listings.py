from watcher.listings import clean_listings, normalize_key, normalize_url
from watcher.models import Listing

BASE = "https://jp.mercari.com/search?keyword=x"


def lst(url, price, matches=True, key=None, title="Item"):
    return Listing(url=url, title=title, price=price, why_matches="fits", matches=matches, verified=True, item_key=key)


def test_normalize_url():
    assert normalize_url("/item/m1", "https://jp.mercari.com") == "https://jp.mercari.com/item/m1"
    assert normalize_url("https://JP.Mercari.com/item/m1?gclid=1&utm_x=2#p") == "https://jp.mercari.com/item/m1"
    assert normalize_url("https://x.jp/s?b=1&a=2") == normalize_url("https://x.jp/s?a=2&b=1")


def test_normalize_key():
    assert normalize_key(" Maison A  3F ") == "maison a 3f"
    assert normalize_key("") is None and normalize_key(None) is None


def test_clean_keeps_agent_order():
    # the agent refers to listings as #1, #2…; reordering would make its numbers point at the wrong card
    out = clean_listings([lst("https://x.jp/1", 300), lst("https://x.jp/2", None), lst("https://x.jp/3", 100)])
    assert [i.url for i in out] == ["https://x.jp/1", "https://x.jp/2", "https://x.jp/3"]


def test_key_duplicate_keeps_first_position_cheapest_item():
    out = clean_listings([lst("https://x.jp/a", 61000, key="B 2F"), lst("https://x.jp/n", 5),
                          lst("https://x.jp/c", 59000, key="b 2f")])
    assert [(i.url, i.price) for i in out] == [("https://x.jp/c", 59000), ("https://x.jp/n", 5)]


def test_clean_drops_non_matching():
    assert clean_listings([lst("https://x.jp/1", 1, matches=False)]) == []


def test_clean_drops_js_and_self_links():
    out = clean_listings([lst("javascript:void(0);", 1), lst("#", 1), lst(BASE, 1), lst("/item/m9", 5)], BASE)
    assert [i.url for i in out] == ["https://jp.mercari.com/item/m9"]


def test_clean_dedupes_url_variants():
    out = clean_listings([lst("https://x.jp/1?utm_source=a", 5), lst("https://x.jp/1#p", 5)])
    assert len(out) == 1


def test_clean_dedupes_key_keeps_cheapest():
    out = clean_listings([lst("https://x.jp/1", 61000, key="B 2F"), lst("https://x.jp/2", 59000, key="b  2f")])
    assert [(i.url, i.price) for i in out] == [("https://x.jp/2", 59000)]
