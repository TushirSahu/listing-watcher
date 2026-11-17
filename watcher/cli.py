import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from watcher.agent import ChatSession, ConfigError, make_llm
from watcher.listings import clean_listings
from watcher.models import Reply

HELP = "Ask for something to find. Follow up to refine. /new starts a new search, /quit exits."


def render(reply: Reply) -> str:
    lines = [reply.answer]
    for n, item in enumerate(clean_listings(reply.listings), 1):
        price = "?" if item.price is None else f"{item.price:,}"
        lines += [f"  {n}. ¥{price}  {item.title}", f"     {item.why_matches}", f"     {item.url}"]
    return "\n".join(lines)


async def chat_loop(session, read: Callable[[], Awaitable[str]], write: Callable[[str], None]) -> None:
    try:
        while True:
            text = (await read()).strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/new":
                await session.close()
                write("started a new search")
                continue
            write(render(await session.ask(text)))
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await session.close()


def _setup() -> tuple[object, int | None]:
    load_dotenv(Path.cwd() / ".env")
    llm = make_llm(os.environ.get("LLM_PROVIDER", ""), os.environ.get("LLM_MODEL", ""), os.environ)
    cap = os.environ.get("LLM_MAX_CONTEXT_CHARS", "").strip()
    if cap and not (cap.isdigit() and int(cap) > 0):
        raise ConfigError(f"LLM_MAX_CONTEXT_CHARS must be a positive number (got {cap!r})")
    return llm, int(cap) if cap else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="listing-watcher", description="Chat with a browser agent that finds listings.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chat_p = sub.add_parser("chat", help="start a conversation")
    chat_p.add_argument("--show-browser", action="store_true", help="show the browser window")
    web_p = sub.add_parser("web", help="open the chat in your web browser (http://localhost:8000)")
    web_p.add_argument("--show-browser", action="store_true", help="show the agent's browser window")
    web_p.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.getLogger("browser_use").setLevel(logging.WARNING)  # hide per-step agent logs

    try:
        llm, cap = _setup()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    session = ChatSession(llm, headless=not args.show_browser, max_context_chars=cap)
    if args.cmd == "web":
        from watcher.web import create_app

        print(f"open http://localhost:{args.port}  (Ctrl+C to stop)")
        uvicorn.run(create_app(session), host="127.0.0.1", port=args.port, log_level="warning")
        return 0
    print(HELP)

    async def read() -> str:
        return await asyncio.to_thread(input, "\nyou> ")

    try:
        asyncio.run(chat_loop(session, read, lambda text: print(f"agent> {text}")))
    except KeyboardInterrupt:
        pass
    return 0
