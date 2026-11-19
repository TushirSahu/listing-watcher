import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from watcher.agent import DEFAULT_SITES, ChatSession, ConfigError, make_llm
from watcher.history import recap
from watcher.listings import clean_listings
from watcher.models import Reply

HELP = "Ask for something to find. Follow up to refine. /new starts a new search, /quit exits."


def render(reply: Reply) -> str:
    lines = [reply.answer]
    for n, item in enumerate(clean_listings(reply.listings), 1):
        price = "?" if item.price is None else f"{item.price:,}"
        lines += [f"  {n}. ¥{price}  {item.title}", f"     {item.why_matches}", f"     {item.url}"]
    return "\n".join(lines)


async def chat_loop(session, read: Callable[[], Awaitable[str]], write: Callable[[str], None],
                    shop_search=None) -> None:
    found, unseen = False, []  # unseen: shop turns the single agent hasn't been told about yet
    try:
        while True:
            text = (await read()).strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/new":
                await session.close()
                found, unseen = False, []
                write("started a new search")
                continue
            reply = None
            if shop_search and not found:  # same routing as the web page; None = not a shop request
                try:
                    reply = await shop_search(text, None)
                except Exception as e:
                    write(f"shop search failed ({type(e).__name__}: {e}), asking the browser agent")
            if reply is not None:
                listings = [i.model_dump() for i in clean_listings(reply.listings)]
                unseen += [{"role": "you", "text": text, "listings": []},
                           {"role": "agent", "text": reply.answer, "listings": listings}]
            else:
                reply = await session.ask(f"{recap(unseen)}\n\nNew message: {text}" if unseen else text)
                unseen = []
            found = found or bool(clean_listings(reply.listings))
            write(render(reply))
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await session.close()


def _positive_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    if not (value.isdigit() and int(value) > 0):
        raise ConfigError(f"{name} must be a positive number (got {value!r})")
    return int(value)


def _allowed_sites() -> list[str] | None:
    extra = [s.strip() for s in os.environ.get("ALLOWED_SITES", "").split(",") if s.strip()]
    if "*" in extra:
        print("warning: ALLOWED_SITES=* lets the agent's browser open any site", file=sys.stderr)
        return None
    return DEFAULT_SITES + extra


def _session(headless: bool) -> ChatSession:
    load_dotenv(Path.cwd() / ".env")
    llm = make_llm(os.environ.get("LLM_PROVIDER", ""), os.environ.get("LLM_MODEL", ""), os.environ)
    return ChatSession(
        llm,
        headless=headless,
        max_context_chars=_positive_int("LLM_MAX_CONTEXT_CHARS", None),
        allowed_sites=_allowed_sites(),
        injection_check=os.environ.get("INJECTION_CHECK", "on").strip().lower() != "off",
        step_budget=_positive_int("MAX_STEPS_PER_RUN", 600),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="listing-watcher", description="Chat with a browser agent that finds listings.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_p = sub.add_parser("chat", help="start a conversation")
    chat_p.add_argument("--show-browser", action="store_true", help="show the browser window")
    web_p = sub.add_parser("web", help="open the chat in your web browser (http://localhost:8000)")
    web_p.add_argument("--show-browser", action="store_true", help="show the agent's browser window")
    web_p.add_argument("--port", type=int, default=8000)
    web_p.add_argument("--host", default="127.0.0.1", help="bind address (the Docker image uses 0.0.0.0 inside the container)")
    args = parser.parse_args(argv)
    logging.getLogger("browser_use").setLevel(logging.WARNING)  # hide per-step agent logs

    try:
        session = _session(headless=not args.show_browser)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    from watcher.shops import make_shop_search

    shop_search = make_shop_search(session.llm, {
        "headless": session.headless, "max_context_chars": session.max_context_chars,
        "injection_check": session.injection_check, "step_budget": session.step_budget})
    if args.cmd == "web":
        from watcher.history import DEFAULT_PATH, History
        from watcher.web import create_app

        print(f"open http://localhost:{args.port}  (Ctrl+C to stop)")
        history = History(os.environ.get("HISTORY_DB") or DEFAULT_PATH)
        uvicorn.run(create_app(session, history, shop_search), host=args.host, port=args.port, log_level="warning")
        return 0
    print(HELP)

    async def read() -> str:
        return await asyncio.to_thread(input, "\nyou> ")

    try:
        asyncio.run(chat_loop(session, read, lambda text: print(f"agent> {text}"), shop_search))
    except KeyboardInterrupt:
        pass
    return 0
