import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from watcher.listings import clean_listings
from watcher.models import Reply

INDEX = Path(__file__).parent / "static" / "index.html"
_STEP = re.compile(r"step (\d+): (.*)", re.S)
_URL = re.compile(r"""["'](https?://[^"']+)["']""")


def step_text(msg: str) -> str | None:
    """Turn browser-use's 'step N: <first code line>' log into a short feed line."""
    m = _STEP.match(msg)
    if not m:
        return None
    n, code = m[1], m[2]
    if " -> " in code:
        what = "hit an error, retrying"
    elif url := _URL.search(code):
        parts = urlsplit(url[1])
        what = f"opening {parts.netloc}{parts.path.rstrip('/')}"[:80]
    elif "js(" in code:
        what = "reading the page"
    elif "click" in code:
        what = "clicking"
    elif "scroll" in code:
        what = "scrolling"
    else:
        what = "working"
    return f"step {n} · {what}"


class Chat:
    """One shared conversation: the agent session, history, connected pages, busy flag."""

    def __init__(self, session):
        self.session = session
        self.messages: list[dict] = []
        self.sockets: set[WebSocket] = set()
        self.busy = False
        self.loop: asyncio.AbstractEventLoop | None = None

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.sockets.discard(ws)

    def on_step(self, text: str) -> None:
        if self.busy and self.loop:
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self.broadcast({"type": "step", "text": text}))
            )

    async def turn(self, text: str) -> None:
        self.busy, self.loop = True, asyncio.get_running_loop()
        self.messages.append({"role": "you", "text": text, "listings": []})
        await self.broadcast({"type": "message", "role": "you", "text": text})
        try:
            reply = await self.session.ask(text)
        except Exception as e:  # ChatSession shouldn't raise; keep the page usable if it does
            reply = Reply(answer=f"error: {type(e).__name__}: {e}")
        await asyncio.sleep(0)  # let queued step lines go out before the reply
        listings = [item.model_dump() for item in clean_listings(reply.listings)]
        self.messages.append({"role": "agent", "text": reply.answer, "listings": listings})
        self.busy = False
        await self.broadcast({"type": "reply", "text": reply.answer, "listings": listings})

    async def reset(self) -> None:
        await self.session.close()
        self.messages = []
        await self.broadcast({"type": "reset"})


class _StepHandler(logging.Handler):
    def __init__(self, chat: Chat):
        super().__init__(logging.INFO)
        self.chat = chat

    def emit(self, record: logging.LogRecord) -> None:
        if text := step_text(record.getMessage()):
            self.chat.on_step(text)


def create_app(session) -> Starlette:
    chat = Chat(session)
    log = logging.getLogger("browser_use")
    for h in log.handlers:  # keep the terminal quiet; steps go to the page instead
        h.setLevel(logging.WARNING)
    log.setLevel(logging.INFO)
    log.addHandler(_StepHandler(chat))

    async def index(request):
        return FileResponse(INDEX)

    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        chat.sockets.add(ws)
        await ws.send_json({"type": "history", "messages": chat.messages, "busy": chat.busy})
        try:
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "ask" and (text := str(msg.get("text", "")).strip()):
                    if chat.busy:
                        await ws.send_json({"type": "busy"})
                    else:
                        # own task: closing the page doesn't cancel the agent's turn
                        task = asyncio.create_task(chat.turn(text))
                        app.state.tasks.add(task)
                        task.add_done_callback(app.state.tasks.discard)
                elif msg.get("type") == "new" and not chat.busy:
                    await chat.reset()
        except WebSocketDisconnect:
            pass
        finally:
            chat.sockets.discard(ws)

    app = Starlette(routes=[Route("/", index), WebSocketRoute("/ws", ws_endpoint)])
    app.state.chat, app.state.tasks = chat, set()
    return app
