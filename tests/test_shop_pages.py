import asyncio

import httpx

from watcher.shop_pages import PARSERS, fetch_shop

# trimmed from real result pages (2026-11)
HARDOFF = '''<div class="itemcolmn_item "> <a href="https://netmall.hardoff.co.jp/product/6717299/" >
<div class="item-img-square"> <img onerror="util.alterImage(this, '/x.png')" src="https://p1-d9ebd2ee.imageflux.jp/c!/w=231/201447/a.jpg" alt="ヘッドホン|SONY" /> </div>
<div class="item-infowrap"> <div class="item-brand-name">SONY</div> <div class="item-name">ヘッドホン</div> <div class="item-code">WH-1000XM5</div>
<div class="item-price"> <span class="item-price-icon"> <img src="/assets/images/common/rank/icon-rank-s-b.svg" alt="b"/> </span>
<span class="font-en item-price-en"> 27,500<span class="item-price-jp">円</span> </span> </div> </div> </a> </div>'''

BOOKOFF = '''<div class="productItem__inner"> <a href="/used/0020695349" class="productItem__image js-hoverItemLink">
<img src="https://content.bookoff.co.jp/goodsimages/LL/002069/0020695349LL.jpg" alt="ONE PIECE(巻百十三) ジャンプC" /> </a>
<p class="productItem__title">ONE PIECE(巻百十三) ジャンプC</p> <p class="productItem__price"> &yen;495<span class="productItem__moneyUnit">円</span><small>定価より77円（13%）おトク</small></p>
<p class="productItem__stock"><span class="productItem__stock--alert">在庫あり</span></p></div>
<div class="productItem__inner"> <a href="/used/0099"><img src="/b.jpg" alt="x"/></a> <p class="productItem__title">Gone</p>
<p class="productItem__price"> &yen;100<span>円</span></p> <p class="productItem__stock"><span>在庫なし</span></p></div>'''

TREFAC = '''<li class="pj-search_item"> <a class="cm-itemlist_itemcode_link" href="/item/1029007938733034" >
<div class="cm-itemlist_image"> <img src="/images/1029007938733034/340/a.jpeg" width="340" height="340" alt="SONY (ソニー) ワイヤレスヘッドホン WH-1000XM5 小キズ有"> </div>
<div class="cm-itemlist_price_wrap"> <p class="cm-typo_head4 cm-itemlist_price is-sale">￥16,500</p> </div> </a>
<div class="cm-itemlist_shop_link"> <a href="/search?step=1&shop=34"> <img src="/logo.svg" alt=""> <span>北越谷店</span> </a> </div> </li>'''


def test_hardoff():
    [i] = PARSERS["hardoff"](HARDOFF)
    assert i.url == "https://netmall.hardoff.co.jp/product/6717299/"
    assert i.title == "SONY ヘッドホン WH-1000XM5" and i.price == 27500 and i.condition_grade == "B"
    assert i.image_url.startswith("https://p1-d9ebd2ee.imageflux.jp/")


def test_bookoff_skips_out_of_stock():
    [i] = PARSERS["bookoff"](BOOKOFF)
    assert i.url == "https://shopping.bookoff.co.jp/used/0020695349"
    assert i.title == "ONE PIECE(巻百十三) ジャンプC" and i.price == 495


def test_trefac():
    [i] = PARSERS["trefac"](TREFAC)
    assert i.url == "https://ec.treasure-f.com/item/1029007938733034" and i.price == 16500
    assert i.branch == "北越谷店" and "WH-1000XM5" in i.title
    assert i.image_url == "https://ec.treasure-f.com/images/1029007938733034/340/a.jpeg"


def test_fetch_shop_filters_budget_and_handles_errors():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=HARDOFF)

    items = asyncio.run(fetch_shop("hardoff", "WH-1000XM5", 30000, httpx.MockTransport(handler)))
    assert len(items) == 1 and items[0].shop == "Hard Off"
    assert seen == ["https://netmall.hardoff.co.jp/search/?q=WH-1000XM5&exso=1"]
    assert asyncio.run(fetch_shop("hardoff", "x", 20000, httpx.MockTransport(handler))) == []

    blocked = httpx.MockTransport(lambda r: httpx.Response(403, text="no"))
    try:
        asyncio.run(fetch_shop("trefac", "x", None, blocked))
        assert False, "403 must raise so the caller can report the shop as failed"
    except httpx.HTTPStatusError:
        pass
