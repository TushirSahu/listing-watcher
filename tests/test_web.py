import asyncio
import logging

from starlette.testclient import TestClient

from watcher.models import Listing, Reply
from watcher.web import create_app, step_text


def test_step_text():
    assert step_text('step 3: new_tab("https://jp.mercari.com/item/m1")') == "step 3 · opening jp.mercari.com/item/m1"
    assert step_text("step 4: data = js('''") == "step 4 · reading the page"
    assert step_text("step 5: click(12)") == "step 5 · clicking"
    assert step_text("step 6: scroll(down)") == "step 6 · scrolling"
    assert step_text("step 7: js('x') -> SyntaxError: bad") == "step 7 · hit an error, retrying"
    assert step_text("step 8: listings_to_check = [1]") == "step 8 · working"
    assert step_text("Agent started") is None


GOOD = Listing(url="https://x.jp/1", title="XM5", price=19666, why_matches="case", matches=True, verified=True,
               image_url="https://img.x.jp/1.jpg")
BAD = Listing(url="javascript:void(0)", title="bad", price=1, why_matches="", matches=True, verified=True)


class FakeSession:
    def __init__(self, reply=None, log=None):
        self.asked, self.closed = [], 0
        self.reply = reply or Reply(answer="Found 1.", listings=[GOOD, BAD])
        self.log = log

    async def ask(self, text):
        self.asked.append(text)
        if self.log:
            logging.getLogger("browser_use").info(self.log)
        await asyncio.sleep(0)
        return self.reply

    async def close(self):
        self.closed += 1


def until(ws, kind):
    msgs = []
    while True:
        m = ws.receive_json()
        msgs.append(m)
        if m["type"] == kind:
            return msgs


def test_index_served():
    r = TestClient(create_app(FakeSession())).get("/")
    assert r.status_code == 200 and "listing-watcher" in r.text


def test_ask_reply_and_clean_listings():
    s = FakeSession()
    with TestClient(create_app(s)) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "history", "messages": [], "busy": False, "steps": []}
        ws.send_json({"type": "ask", "text": "find xm5"})
        msgs = until(ws, "reply")
        assert {"type": "message", "role": "you", "text": "find xm5"} in msgs
        reply = msgs[-1]
        assert reply["text"] == "Found 1." and [l["url"] for l in reply["listings"]] == ["https://x.jp/1"]
        assert reply["listings"][0]["image_url"] == "https://img.x.jp/1.jpg"
    assert s.asked == ["find xm5"]


def test_reconnect_gets_history():
    app = create_app(FakeSession())
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "ask", "text": "a"})
            until(ws, "reply")
        with c.websocket_connect("/ws") as ws:
            h = ws.receive_json()
            assert h["type"] == "history" and h["busy"] is False
            assert [(m["role"], m["text"]) for m in h["messages"]] == [("you", "a"), ("agent", "Found 1.")]
            assert len(h["messages"][1]["listings"]) == 1


def test_error_reply_clears_busy():
    app = create_app(FakeSession(reply=Reply(answer="error: quota")))
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        assert until(ws, "reply")[-1]["text"] == "error: quota"
        ws.send_json({"type": "ask", "text": "b"})
        until(ws, "reply")


def test_busy_rejects_second_ask():
    s = FakeSession()
    app = create_app(s)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        app.state.chat.busy = True
        ws.send_json({"type": "ask", "text": "a"})
        assert ws.receive_json() == {"type": "busy"}
    assert s.asked == []


def test_new_resets():
    s = FakeSession()
    app = create_app(s)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        until(ws, "reply")
        ws.send_json({"type": "new"})
        assert until(ws, "reset")[-1] == {"type": "reset"}
    assert s.closed >= 1 and app.state.chat.messages == []


def test_steps_forwarded():
    s = FakeSession(log='step 2: new_tab("https://jp.mercari.com/search?keyword=xm5")')
    with TestClient(create_app(s)) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        msgs = until(ws, "reply")
        assert {"type": "step", "text": "step 2 · searching jp.mercari.com for “xm5”"} in msgs


def test_shutdown_closes_browser():
    s = FakeSession()
    with TestClient(create_app(s)):
        pass
    assert s.closed == 1


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
    asyncio.run(fill_images(items, transport=transport))
    assert [i["image_url"] for i in items] == ["https://img.x.jp/1.jpg", "https://img.x.jp/2.jpg", None,
                                               "https://keep.jp/4.jpg"]


def test_fill_images_survives_errors():
    import httpx

    from watcher.web import fill_images

    def boom(req):
        raise httpx.ConnectError("down")

    items = [{"url": "https://x.jp/1", "image_url": None}]
    asyncio.run(fill_images(items, transport=httpx.MockTransport(boom)))
    assert items[0]["image_url"] is None


def test_history_includes_last_steps():
    app = create_app(FakeSession(log='step 1: screenshot()'))
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "ask", "text": "a"})
            until(ws, "reply")
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["steps"] == ["step 1 · looking at the page"]
