import asyncio
import contextlib
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from watcher.listings import clean_listings
from watcher.models import Reply

INDEX = Path(__file__).parent / "static" / "index.html"
_STEP = re.compile(r"step (\d+): (.*)", re.S)
_URL = re.compile(r"""["'](https?://[^"']+)["']""")
_SEARCH_KEYS = ("keyword", "q", "k", "query", "search")
_OG_IMAGE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)["'][^>]*content=["'](https?://[^"']+)["']""", re.I
)


def step_text(msg: str) -> str | None:
    """Turn browser-use's 'step N: <first code line>' log into a short feed line."""
    m = _STEP.match(msg)
    if not m:
        return None
    n, code = m[1], m[2]
    if " -> " in code:
        what = "hit an error, retrying"
    elif code.lstrip().startswith("#"):
        what = code.lstrip("# ").strip()[:80] or "working"  # the model's own note on what this step does
    elif url := _URL.search(code):
        parts = urlsplit(url[1])
        query = parse_qs(parts.query)
        if kw := next((query[k][0] for k in _SEARCH_KEYS if k in query), None):
            what = f"searching {parts.netloc} for “{kw[:40]}”"
        else:
            what = f"opening {parts.netloc}{parts.path.rstrip('/')}"[:80]
    elif "screenshot" in code:
        what = "looking at the page"
    elif "sleep" in code:
        what = "waiting for the page to load"
    elif "result" in code:
        what = "writing up results"
    elif "js(" in code:
        what = "reading the page"
    elif "click" in code:
        what = "clicking"
    elif "scroll" in code:
        what = "scrolling"
    else:
        what = "working"
    return f"step {n} · {what}"


async def _image_of(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url)
        m = _OG_IMAGE.search(resp.text[:300_000])
        return m[1] if m else None
    except Exception:  # a missing photo is fine; never fail the reply over it
        return None


async def fill_images(items: list[dict], transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Fill missing image_url from each listing page's og:image / twitter:image (models often skip photos)."""
    missing = [i for i in items if not i.get("image_url")]
    if not missing:
        return
    async with httpx.AsyncClient(timeout=6, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        found = await asyncio.gather(*(_image_of(client, i["url"]) for i in missing))
    for item, image in zip(missing, found):
        item["image_url"] = image


class Chat:
    """One shared conversation: the agent session, history, connected pages, busy flag."""

    def __init__(self, session):
        self.session = session
        self.messages: list[dict] = []
        self.steps: list[str] = []  # feed lines of the current/last turn
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
            self.steps.append(text)
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self.broadcast({"type": "step", "text": text}))
            )

    async def turn(self, text: str) -> None:
        self.busy, self.loop, self.steps = True, asyncio.get_running_loop(), []
        self.messages.append({"role": "you", "text": text, "listings": []})
        await self.broadcast({"type": "message", "role": "you", "text": text})
        try:
            reply = await self.session.ask(text)
        except Exception as e:  # ChatSession shouldn't raise; keep the page usable if it does
            reply = Reply(answer=f"error: {type(e).__name__}: {e}")
        await asyncio.sleep(0)  # let queued step lines go out before the reply
        listings = [item.model_dump() for item in clean_listings(reply.listings)]
        await fill_images(listings)
        self.messages.append({"role": "agent", "text": reply.answer, "listings": listings})
        self.busy = False
        await self.broadcast({"type": "reply", "text": reply.answer, "listings": listings})

    async def reset(self) -> None:
        await self.session.close()
        self.messages, self.steps = [], []
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
        await ws.send_json({"type": "history", "messages": chat.messages, "busy": chat.busy, "steps": chat.steps})
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

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        await session.close()  # don't leave the agent's browser running after Ctrl+C

    app = Starlette(routes=[Route("/", index), WebSocketRoute("/ws", ws_endpoint)], lifespan=lifespan)
    app.state.chat, app.state.tasks = chat, set()
    return app
