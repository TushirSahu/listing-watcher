import asyncio


from watcher.web import step_text


async def PUBLIC(host):
    return ["93.184.216.34"]


def test_step_text():
    assert step_text('step 3: new_tab("https://jp.mercari.com/item/m1")') == "step 3 · opening jp.mercari.com/item/m1"
    assert step_text("step 4: data = js('''") == "step 4 · reading the page"
    assert step_text("step 5: click(12)") == "step 5 · clicking"
    assert step_text("step 6: scroll(down)") == "step 6 · scrolling"
    assert step_text("step 7: js('x') -> SyntaxError: bad") == "step 7 · hit an error, retrying"
    assert step_text("step 8: listings_to_check = [1]") == "step 8 · working"
    assert step_text("Agent started") is None


def test_step_text_more():
    assert step_text("step 5: goto_url('https://jp.mercari.com/search?keyword=Sony+WH-1000XM5')") == \
        "step 5 · searching jp.mercari.com for “Sony WH-1000XM5”"
    assert step_text("step 2: screenshot()") == "step 2 · looking at the page"
    assert step_text("step 16: time.sleep(2)") == "step 16 · waiting for the page to load"
    assert step_text("step 21: result_data = {") == "step 21 · writing up results"


def test_fill_images():
    import httpx

    from watcher.web import fill_images

    pages = {
        "https://x.jp/1": '<html><head><meta property="og:image" content="https://img.x.jp/1.jpg"/></head>',
        "https://x.jp/2": '<meta name="twitter:image" content="https://img.x.jp/2.jpg">',
        "https://x.jp/3": "<html>no image</html>",
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=pages.get(str(req.url), "")))
    items = [
        {"url": "https://x.jp/1", "image_url": None},
        {"url": "https://x.jp/2", "image_url": None},
        {"url": "https://x.jp/3", "image_url": None},
        {"url": "https://x.jp/4", "image_url": "https://keep.jp/4.jpg"},
    ]
    asyncio.run(fill_images(items, transport=transport, resolve=PUBLIC))
    assert [i["image_url"] for i in items] == ["https://img.x.jp/1.jpg", "https://img.x.jp/2.jpg", None,
                                               "https://keep.jp/4.jpg"]


def test_fill_images_survives_errors():
    import httpx

    from watcher.web import fill_images

    def boom(req):
        raise httpx.ConnectError("down")

    items = [{"url": "https://x.jp/1", "image_url": None}]
    asyncio.run(fill_images(items, transport=httpx.MockTransport(boom), resolve=PUBLIC))
    assert items[0]["image_url"] is None


def test_step_text_uses_code_comment():
    assert step_text("step 3: # Search Mercari for WH-1000XM5 under budget") == \
        "step 3 · Search Mercari for WH-1000XM5 under budget"
    assert step_text("step 4: #") == "step 4 · working"


def test_fill_images_blocks_unsafe_urls():
    import httpx

    from watcher.web import fill_images

    fetched = []

    def handler(req):
        fetched.append(str(req.url))
        return httpx.Response(200, text='<meta property="og:image" content="https://img.x.jp/1.jpg">')

    async def resolve(host):
        return {"router.local": ["192.168.1.1"], "meta.cloud": ["169.254.169.254"], "loop.jp": ["127.0.0.1"],
                "ok.jp": ["93.184.216.34"]}[host]

    items = [{"url": u, "image_url": None} for u in
             ["http://ok.jp/plain-http", "https://router.local/", "https://meta.cloud/x", "https://loop.jp/",
              "https://10.0.0.5/", "https://ok.jp/item"]]
    asyncio.run(fill_images(items, transport=httpx.MockTransport(handler), resolve=resolve))
    assert fetched == ["https://ok.jp/item"]
    assert [i["image_url"] for i in items] == [None, None, None, None, None, "https://img.x.jp/1.jpg"]


def test_fill_images_ignores_unsafe_image_url():
    import httpx

    from watcher.web import fill_images

    page = '<meta property="og:image" content="http://img.x.jp/insecure.jpg">'
    items = [{"url": "https://ok.jp/item", "image_url": None}]
    asyncio.run(fill_images(items, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page)),
                            resolve=PUBLIC))
    assert items[0]["image_url"] is None
