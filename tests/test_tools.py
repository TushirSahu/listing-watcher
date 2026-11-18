import asyncio

import httpx

from watcher.tools import lookup_commute, make_tools, parse_commute

PAGE = (
    '<li class="time">22:15<!-- -->→<span class="mark">22:52</span><span class="small">37分</span></li>'
    '<li class="fare">440<!-- -->円</li><li class="transfer">乗換：<span class="mark">1<!-- -->回</span></li>'
    '<li class="time">22:25<!-- -->→<!-- -->23:04<span class="small">39分</span></li>'
    '<li class="fare">440<!-- -->円</li><li class="transfer">乗換：<span class="mark">1<!-- -->回</span></li>'
    '<li class="time">22:30<!-- -->→<!-- -->23:45<span class="small">1時間15分</span></li>'
    '<li class="fare">1,020<!-- -->円</li><li class="transfer">乗換：<span class="mark">0<!-- -->回</span></li>'
)


def test_parse_commute_picks_fastest():
    assert parse_commute(PAGE) == "37 min, 1 transfer, ¥440"


def test_parse_commute_hours_and_no_transfer():
    one = PAGE.split('<li class="time">22:30')[1]
    assert parse_commute('<li class="time">22:30' + one) == "75 min, direct, ¥1,020"


def test_parse_commute_no_route():
    assert parse_commute("<html>候補がありません</html>") == "no route found"


def test_commute_time_fixed_host_and_encoding():
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, text=PAGE)

    out = asyncio.run(lookup_commute("吹田", "石橋阪大前&to=evil", transport=httpx.MockTransport(handler)))
    assert out == "37 min, 1 transfer, ¥440"
    url = seen[0]
    assert url.host == "transit.yahoo.co.jp" and url.path == "/search/result"
    assert url.params["from"] == "吹田" and url.params["to"] == "石橋阪大前&to=evil"


def test_commute_time_never_raises():
    def boom(req):
        raise httpx.ConnectError("down")

    assert asyncio.run(lookup_commute("a", "b", transport=httpx.MockTransport(boom))) == "no route found (lookup failed)"


def _tool(tools, name):
    return next(t for t in tools.functions if t.name == name)


def test_tool_set():
    assert {t.name for t in make_tools().functions} == {"commute_time", "report_listing"}


def test_report_listing_streams_clean_listing():
    got = []
    tools = make_tools(lambda item: got.append(item) or len(got))
    out = asyncio.run(_tool(tools, "report_listing").function(
        url="https://JP.mercari.com/item/m1?utm_source=x", title="XM5", price=19000, why_matches="case", verified=True))
    assert out == "shown as #1"
    assert got[0].url == "https://jp.mercari.com/item/m1" and got[0].price == 19000


def test_report_listing_rejects_bad_link():
    got = []
    tools = make_tools(got.append)
    out = asyncio.run(_tool(tools, "report_listing").function(
        url="javascript:void(0)", title="x", price=1, why_matches="", verified=True))
    assert out.startswith("not shown") and got == []


def test_report_listing_explains_relative_link():
    got = []
    out = asyncio.run(_tool(make_tools(got.append), "report_listing").function(
        url="/item/m123", title="x", price=1, why_matches="", verified=True))
    assert got == [] and "full https link" in out and "https://jp.mercari.com/item/m123" in out


def test_report_listing_carries_shop_fields():
    got = []
    asyncio.run(_tool(make_tools(got.append), "report_listing").function(
        url="https://netmall.hardoff.co.jp/product/1", title="x", price=1, why_matches="", verified=True,
        branch="ハードオフ吹田店", branch_station="吹田", condition_grade="B"))
    assert (got[0].branch, got[0].branch_station, got[0].condition_grade) == ("ハードオフ吹田店", "吹田", "B")
