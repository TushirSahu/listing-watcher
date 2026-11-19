import asyncio
import logging

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from watcher.history import History
from watcher.models import Listing, Reply
from watcher.web import create_app

WS = "ws://localhost:8000/ws"
BASE = "http://localhost:8000"
GOOD = Listing(url="https://x.jp/1", title="XM5", price=19666, why_matches="case", matches=True, verified=True,
               image_url="https://img.x.jp/1.jpg")
BAD = Listing(url="javascript:void(0)", title="bad", price=1, why_matches="", matches=True, verified=True)


class FakeSession:
    def __init__(self, reply=None, log=None, stream=(), hang=False):
        self.asked, self.closed = [], 0
        self.reply = reply or Reply(answer="Found 1.", listings=[GOOD, BAD])
        self.log, self.stream, self.hang = log, stream, hang
        self.on_listing = None
        self.last_steps, self.last_cost = 12, 0.003

    async def ask(self, text):
        self.asked.append(text)
        if self.log:
            logging.getLogger("browser_use").info(self.log)
        for item in self.stream:
            self.on_listing(item)
            await asyncio.sleep(0)
        if self.hang:
            await asyncio.sleep(3600)
        await asyncio.sleep(0)
        return self.reply

    async def close(self):
        self.closed += 1


def app_for(tmp_path, session=None):
    return create_app(session or FakeSession(), History(tmp_path / "h.db"))


def until(ws, kind):
    msgs = []
    while True:
        m = ws.receive_json()
        msgs.append(m)
        if m["type"] == kind:
            return msgs


def connect(c):
    ws = c.websocket_connect(WS)
    return ws


def test_index_served(tmp_path):
    r = TestClient(app_for(tmp_path), base_url=BASE).get("/")
    assert r.status_code == 200 and "Thrift Scout" in r.text


def test_every_event_is_versioned(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        first = ws.receive_json()
        assert first == {"v": 1, "type": "history", "conv": None, "messages": [], "busy": False, "steps": []}
        ws.send_json({"type": "ask", "text": "find xm5"})
        for m in until(ws, "reply"):
            assert m["v"] == 1 and m["conv"]


def test_ask_saves_and_cleans(tmp_path):
    s = FakeSession()
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "find xm5"})
        msgs = until(ws, "reply")
    reply = msgs[-1]
    assert {"you": "find xm5"} == {m["role"]: m["text"] for m in msgs if m["type"] == "message"}
    assert [l["url"] for l in reply["listings"]] == ["https://x.jp/1"]
    assert reply["steps"] == 12 and reply["cost"] == 0.003
    h = History(tmp_path / "h.db")
    saved = h.messages(reply["conv"])
    assert [(m["role"], m["text"]) for m in saved] == [("you", "find xm5"), ("agent", "Found 1.")]
    assert saved[1]["listings"][0]["url"] == "https://x.jp/1" and saved[1]["steps"] == 12
    assert h.conversations()[0]["title"] == "find xm5"


def test_streamed_listings_then_final(tmp_path):
    s = FakeSession(stream=[GOOD, GOOD.model_copy(update={"url": "https://x.jp/2", "price": 18000})])
    with TestClient(app_for(tmp_path, s), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        msgs = until(ws, "reply")
    streamed = [m for m in msgs if m["type"] == "listing"]
    assert [(m["n"], m["listing"]["url"]) for m in streamed] == [(1, "https://x.jp/1"), (2, "https://x.jp/2")]
    assert msgs.index(streamed[-1]) < len(msgs) - 1  # cards arrive before the final reply


def test_restart_loads_latest_conversation(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        conv = until(ws, "reply")[-1]["conv"]
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        h = ws.receive_json()
    assert h["conv"] == conv and [m["text"] for m in h["messages"]] == ["a", "Found 1."]


def test_resumed_conversation_sends_recap_once(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "find xm5"})
        until(ws, "reply")
    s = FakeSession()
    with TestClient(app_for(tmp_path, s), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "check #1"})
        until(ws, "reply")
        ws.send_json({"type": "ask", "text": "and #2"})
        until(ws, "reply")
    assert s.asked[0].startswith("Earlier in this conversation") and "find xm5" in s.asked[0]
    assert s.asked[0].endswith("check #1")
    assert s.asked[1] == "and #2"


def test_list_and_open(tmp_path):
    s = FakeSession()
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "first"})
        first = until(ws, "reply")[-1]["conv"]
        ws.send_json({"type": "new"})
        until(ws, "reset")
        ws.send_json({"type": "ask", "text": "second"})
        until(ws, "reply")
        ws.send_json({"type": "list"})
        items = until(ws, "conversations")[-1]["items"]
        assert [i["title"] for i in items] == ["second", "first"]
        ws.send_json({"type": "open", "conv": first})
        h = until(ws, "history")[-1]
        assert h["conv"] == first and [m["text"] for m in h["messages"]] == ["first", "Found 1."]
    assert s.closed >= 2  # new + open each reset the agent


def test_open_unknown_conversation_errors(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "open", "conv": "nope"})
        assert ws.receive_json()["type"] == "error"


def test_cancel_stops_turn(tmp_path):
    s = FakeSession(hang=True)
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "slow"})
        conv = until(ws, "message")[-1]["conv"]
        ws.send_json({"type": "cancel"})
        assert until(ws, "cancelled")[-1]["type"] == "cancelled"
        assert app.state.chat.busy is False
    saved = History(tmp_path / "h.db").messages(conv)
    assert [(m["role"], m["text"]) for m in saved] == [("you", "slow"), ("agent", "Cancelled.")]
    assert s.closed >= 1


def test_busy_rejects_second_ask(tmp_path):
    s = FakeSession()
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        app.state.chat.busy = True
        ws.send_json({"type": "ask", "text": "a"})
        assert ws.receive_json()["type"] == "busy"
    assert s.asked == []


def test_unknown_type_ignored(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "dance"})
        ws.send_json({"type": "list"})
        assert ws.receive_json()["type"] == "conversations"


def test_error_reply_saved_and_clears_busy(tmp_path):
    app = app_for(tmp_path, FakeSession(reply=Reply(answer="error: quota")))
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "a"})
        assert until(ws, "reply")[-1]["text"] == "error: quota"
        ws.send_json({"type": "ask", "text": "b"})
        until(ws, "reply")


def test_steps_forwarded_and_kept(tmp_path):
    s = FakeSession(log='step 2: new_tab("https://jp.mercari.com/search?keyword=xm5")')
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c:
        with c.websocket_connect(WS) as ws:
            ws.receive_json()
            ws.send_json({"type": "ask", "text": "a"})
            msgs = until(ws, "reply")
        with c.websocket_connect(WS) as ws:
            steps = ws.receive_json()["steps"]
    assert any(m["type"] == "step" and m["text"] == "step 2 · searching jp.mercari.com for “xm5”" for m in msgs)
    assert steps == ["step 2 · searching jp.mercari.com for “xm5”"]


def test_shutdown_closes_browser(tmp_path):
    s = FakeSession()
    with TestClient(app_for(tmp_path, s), base_url=BASE):
        pass
    assert s.closed == 1


def test_rejects_foreign_host(tmp_path):
    assert TestClient(app_for(tmp_path), base_url="http://evil.example").get("/").status_code == 400


def test_rejects_cross_site_websocket(tmp_path):
    s = FakeSession()
    with TestClient(app_for(tmp_path, s), base_url=BASE) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(WS, headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()
    assert s.asked == []


def test_accepts_same_origin_websocket(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as c:
        with c.websocket_connect(WS, headers={"origin": "http://localhost:8000"}) as ws:
            assert ws.receive_json()["type"] == "history"


def test_cancel_keeps_cards_found_so_far(tmp_path):
    s = FakeSession(stream=[GOOD], hang=True)
    app = app_for(tmp_path, s)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "slow"})
        conv = until(ws, "listing")[-1]["conv"]
        ws.send_json({"type": "cancel"})
        cancelled = until(ws, "cancelled")[-1]
    assert [l["url"] for l in cancelled["listings"]] == ["https://x.jp/1"]
    saved = History(tmp_path / "h.db").messages(conv)
    assert saved[-1]["text"] == "Cancelled." and saved[-1]["listings"][0]["url"] == "https://x.jp/1"


def test_first_search_goes_to_shop_graph_then_follow_up_to_agent(tmp_path):
    from watcher.shops import current_chain
    calls = []

    async def shop_search(text, on_listing):
        calls.append(text)
        current_chain.set("Hard Off")
        logging.getLogger("browser_use").info("step 3: # check the condition grade")
        on_listing(GOOD)
        await asyncio.sleep(0)
        return Reply(answer="Searched 4 shops", listings=[GOOD])

    s = FakeSession()
    app = create_app(s, History(tmp_path / "h.db"), shop_search=shop_search)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "xm5 at second-hand shops"})
        msgs = until(ws, "reply")
        ws.send_json({"type": "ask", "text": "check #1"})
        until(ws, "reply")
    assert calls == ["xm5 at second-hand shops"]
    assert msgs[-1]["text"] == "Searched 4 shops" and any(m["type"] == "listing" for m in msgs)
    assert any(m["type"] == "step" and m["text"].startswith("[Hard Off] step 3") for m in msgs)
    assert s.asked[0].startswith("Earlier in this conversation") and s.asked[0].endswith("check #1")


def test_marketplace_request_falls_back_to_agent(tmp_path):
    async def shop_search(text, on_listing):
        return None

    s = FakeSession()
    app = create_app(s, History(tmp_path / "h.db"), shop_search=shop_search)
    with TestClient(app, base_url=BASE) as c, c.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "text": "xm5 on mercari"})
        until(ws, "reply")
    assert s.asked == ["xm5 on mercari"]
