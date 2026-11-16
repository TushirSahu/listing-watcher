import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from watcher.agent import make_llm, run_watch
from watcher.config import ConfigError, Watch, load_watches
from watcher.decide import process
from watcher.notify import send_message
from watcher.store import Store

TICK_SECONDS = 60


def due(watches: list[Watch], store: Store, now: datetime) -> list[Watch]:
    out = []
    for w in watches:
        last = store.last_run(w.name)
        if last is None or now - last >= w.every:
            out.append(w)
    return out


async def run_due(watches: list[Watch], store: Store, llm: object, now: datetime, headless: bool,
                  send: Callable[[str], bool]) -> None:
    for w in due(watches, store, now):
        process(w, await run_watch(w, llm, headless=headless), store, send=send)
        store.set_last_run(w.name, now)


def _setup(config: str) -> tuple[list[Watch], object]:
    load_dotenv(Path.cwd() / ".env")
    watches = load_watches(Path(config))
    llm = make_llm(os.environ.get("LLM_PROVIDER", ""), os.environ.get("LLM_MODEL", ""), os.environ)
    return watches, llm


def _print_result(result) -> None:
    print(f"searched={result.searched} listings_seen={result.listings_seen} "
          f"blocked_or_lost={result.blocked_or_lost} note={result.note!r}")
    for item in result.listings:
        price = "?" if item.price is None else f"¥{item.price:,}"
        print(f"- [{'MATCH' if item.matches else 'no'}{'' if item.verified else ', unverified'}] "
              f"{item.title} - {price}\n  {item.why_matches}\n  {item.url}")


async def _loop(watches, store, llm, headless) -> None:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    send = lambda text: send_message(text, token, chat_id)
    names = ", ".join(w.name for w in watches)
    print(f"watching {len(watches)} watch(es): {names}. Ctrl+C to stop.")
    while True:
        await run_due(watches, store, llm, datetime.now(), headless, send)
        await asyncio.sleep(TICK_SECONDS)


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="watches.yaml", help="watch list file (default: watches.yaml)")
    common.add_argument("--show-browser", action="store_true", help="show the browser window")
    parser = argparse.ArgumentParser(prog="listing-watcher", description="Browser agent that watches listing sites.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", parents=[common], help="check all watches on their schedule, send alerts")
    run_p.add_argument("--db", default="state.db", help="memory file (default: state.db)")
    check_p = sub.add_parser("check", parents=[common], help="test one watch once; prints only, no alerts or memory")
    check_p.add_argument("name")
    args = parser.parse_args(argv)
    logging.getLogger("browser_use").setLevel(logging.WARNING)  # hide per-step agent logs

    try:
        watches, llm = _setup(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    headless = not args.show_browser

    if args.cmd == "check":
        watch = next((w for w in watches if w.name == args.name), None)
        if watch is None:
            print(f"config error: no watch named {args.name!r}", file=sys.stderr)
            return 2
        _print_result(asyncio.run(run_watch(watch, llm, headless=headless)))
        return 0

    try:
        asyncio.run(_loop(watches, Store(args.db), llm, headless))
    except KeyboardInterrupt:
        print("stopped")
    return 0
