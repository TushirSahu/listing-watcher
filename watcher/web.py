import asyncio
import contextlib
import ipaddress
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from watcher.history import History, recap
from watcher.listings import clean_listings
from watcher.shops import current_chain
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


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443)
    return [info[4][0] for info in infos]


async def _is_public_https(url: str, resolve) -> bool:
    """Only fetch https URLs whose host resolves to public internet addresses (no LAN, loopback, metadata IPs)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        addrs = await resolve(parts.hostname)  # an IP literal resolves to itself
        return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)
    except Exception:
        return False


async def _image_of(client: httpx.AsyncClient, url: str, resolve) -> str | None:
    try:
        if not await _is_public_https(url, resolve):
            return None
        resp = await client.get(url)
        m = _OG_IMAGE.search(resp.text[:300_000])
        return m[1] if m and m[1].startswith("https://") else None
    except Exception:  # a missing photo is fine; never fail the reply over it
        return None


async def fill_images(items: list[dict], transport: httpx.AsyncBaseTransport | None = None, resolve=None) -> None:
    """Fill missing image_url from each listing page's og:image / twitter:image (models often skip photos).
    The URLs come from model output, so only public https pages are fetched and redirects aren't followed."""
    missing = [i for i in items if not i.get("image_url")]
    resolve = resolve or _system_resolve
    # note: DNS is checked before the request, not pinned for it (small rebinding window); fine for a local tool
    async with httpx.AsyncClient(timeout=6, follow_redirects=False, transport=transport,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        found = await asyncio.gather(*(_image_of(client, i["url"], resolve) for i in missing))
    for item, image in zip(missing, found):
        item["image_url"] = image


PROTOCOL = 1


class Chat:
    """The shared conversation: agent session, saved history, connected pages, the running turn.
    Event protocol v1: every event is {"v": 1, "type": ..., "conv": id, ...}; unknown client types are ignored."""

    def __init__(self, session, history: History, shop_search=None):
        self.session, self.history, self.shop_search = session, history, shop_search
        self.session.on_listing = self.on_listing
        self.sockets: set[WebSocket] = set()
        self.busy, self.task, self.loop = False, None, None
        self.steps: list[str] = []          # feed lines of the current/last turn
        self.streamed: list[dict] = []      # provisional cards of the current turn
        self.load(history.latest())

    def load(self, conv: str | None) -> None:
        self.conv = conv
        self.messages = self.history.messages(conv) if conv else []
        self.needs_recap = bool(self.messages)  # a fresh agent must be told what came before

    def event(self, type_: str, **fields) -> dict:
        return {"v": PROTOCOL, "type": type_, "conv": self.conv, **fields}

    def state(self) -> dict:
        return self.event("history", messages=self.messages, busy=self.busy, steps=self.steps)

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.sockets.discard(ws)

    def _emit_soon(self, msg: dict) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self.broadcast(msg)))

    def on_step(self, text: str) -> None:
        if self.busy:
            self.steps.append(text)
            self._emit_soon(self.event("step", text=text))

    def on_listing(self, item) -> int | None:
        if not self.busy:
            return None
        self.streamed.append(item.model_dump())
        n = len(self.streamed)
        self._emit_soon(self.event("listing", listing=self.streamed[-1], n=n))
        return n

    def start(self, text: str) -> None:
        if self.conv is None:
            self.conv, self.messages, self.needs_recap = self.history.new_conversation(text), [], False
        self.busy, self.steps, self.streamed = True, [], []
        self.task = asyncio.create_task(self.turn(text))  # own task: closing the page doesn't stop it

    async def turn(self, text: str) -> None:
        self.loop = asyncio.get_running_loop()
        self.history.add_message(self.conv, "you", text)
        self.messages.append({"role": "you", "text": text, "listings": [], "steps": None, "cost": None})
        await self.broadcast(self.event("message", role="you", text=text))
        prompt = f"{recap(self.messages[:-1])}\n\nNew message: {text}" if self.needs_recap else text
        first_search = not any(m["listings"] for m in self.messages)
        try:
            reply = None
            if self.shop_search and first_search:  # multi-agent shop search; None = not a shop request
                reply = await self.shop_search(text, self.on_listing)
            if reply is not None:
                self.needs_recap = True  # follow-ups go to the single agent, which needs the results recapped
            else:
                reply = await self.session.ask(prompt)
                self.needs_recap = False
        except asyncio.CancelledError:
            await self.session.close()  # agent state is unknown after a cancel; next turn starts fresh
            self.needs_recap = True
            partial = self.streamed  # cards confirmed before the stop are still real results
            self._finish("Cancelled.", partial, None, None)
            await self.broadcast(self.event("cancelled", listings=partial))
            return
        except Exception as e:  # ChatSession shouldn't raise; keep the page usable if it does
            reply = Reply(answer=f"error: {type(e).__name__}: {e}")
        await asyncio.sleep(0)  # let queued step/listing events go out before the reply
        listings = [item.model_dump() for item in clean_listings(reply.listings)]
        await fill_images(listings)
        steps, cost = getattr(self.session, "last_steps", None), getattr(self.session, "last_cost", None)
        self._finish(reply.answer, listings, steps, cost)
        await self.broadcast(self.event("reply", text=reply.answer, listings=listings, steps=steps, cost=cost))

    def _finish(self, text: str, listings: list[dict], steps, cost) -> None:
        self.history.add_message(self.conv, "agent", text, listings, steps, cost)
        self.messages.append({"role": "agent", "text": text, "listings": listings, "steps": steps, "cost": cost})
        self.busy, self.task = False, None

    async def switch(self, conv: str | None) -> None:
        await self.session.close()
        self.load(conv)
        self.steps, self.streamed = [], []
        await self.broadcast(self.state() if conv else self.event("reset"))


class _StepHandler(logging.Handler):
    def __init__(self, chat: Chat):
        super().__init__(logging.INFO)
        self.chat = chat

    def emit(self, record: logging.LogRecord) -> None:
        if text := step_text(record.getMessage()):
            chain = current_chain.get()  # set per parallel shop agent
            self.chat.on_step(f"[{chain}] {text}" if chain else text)


def create_app(session, history: History | None = None, shop_search=None) -> Starlette:
    chat = Chat(session, history or History(), shop_search)
    log = logging.getLogger("browser_use")
    for h in log.handlers:  # keep the terminal quiet; steps go to the page instead
        h.setLevel(logging.WARNING)
    log.setLevel(logging.INFO)
    log.addHandler(_StepHandler(chat))

    async def index(request):
        return FileResponse(INDEX)

    async def ws_endpoint(ws: WebSocket):
        # browsers always send Origin; refuse pages from other sites (cross-site WebSocket hijacking)
        origin, host = ws.headers.get("origin"), ws.headers.get("host")
        if origin and origin not in (f"http://{host}", f"https://{host}"):
            await ws.close(code=1008)
            return
        await ws.accept()
        chat.sockets.add(ws)
        await ws.send_json(chat.state())
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "ask" and (text := str(msg.get("text", "")).strip()):
                    if chat.busy:
                        await ws.send_json(chat.event("busy"))
                    else:
                        chat.start(text)
                elif kind == "cancel" and chat.busy and chat.task:
                    chat.task.cancel()
                elif kind == "list":
                    await ws.send_json(chat.event("conversations", items=chat.history.conversations()))
                elif kind == "new" and not chat.busy:
                    await chat.switch(None)
                elif kind == "open" and not chat.busy:
                    conv = str(msg.get("conv", ""))
                    if chat.history.exists(conv):
                        await chat.switch(conv)
                    else:
                        await ws.send_json(chat.event("error", text="That conversation no longer exists."))
                # anything else: ignored (forward compatible)
        except WebSocketDisconnect:
            pass
        finally:
            chat.sockets.discard(ws)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        if chat.task:
            chat.task.cancel()
        await session.close()  # don't leave the agent's browser running after Ctrl+C

    app = Starlette(
        routes=[Route("/", index), WebSocketRoute("/ws", ws_endpoint)],
        # only answer requests addressed to this machine by name (blocks DNS-rebinding pages)
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])],
        lifespan=lifespan,
    )
    app.state.chat = chat
    return app
