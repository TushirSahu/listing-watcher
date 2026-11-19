"""Score search_shops on fixed requests against the real shop sites. No AI, no API key.

    python evals/run.py              # all cases
    python evals/run.py xm5 switch   # only cases whose id contains one of these words

Metrics per case: results, precision (titles containing the item), budget violations, links off the shop's host,
share of results with a train time (pick-up cases), seconds. Exits 1 if any case misses its expectations."""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from watcher.mcp_server import search_shops
from watcher.shops import CHAINS

CASES = Path(__file__).with_name("cases.json")
HOSTS = tuple(c.site.removeprefix("*") for c in CHAINS.values())


def norm(s: str) -> str:
    return "".join(s.lower().split()).replace("-", "").replace("・", "")


def score(case: dict, out: dict, seconds: float) -> dict:
    res = out["results"]
    n = len(res)
    words = [norm(w) for w in case.get("title_has", [])]
    hit = sum(1 for r in res if any(w in norm(r["title"]) for w in words)) if words else n
    budget = case.get("max_price")
    m = {
        "results": n,
        "precision": round(hit / n, 2) if n else None,
        "over_budget": sum(1 for r in res if budget and (r.get("price") or 0) > budget),
        "off_host": sum(1 for r in res if not (r["url"].startswith("https://")
                                               and any(r["url"].split("/")[2].endswith(h) for h in HOSTS))),
        "timed": round(sum(" min from " in r.get("why_matches", "") for r in res) / n, 2) if n else None,
        "failed": out["failed"],
        "seconds": round(seconds, 1),
    }
    problems = []
    if n < case.get("min_results", 1):
        problems.append("too few results")
    if "max_results" in case and n > case["max_results"]:
        problems.append("too many results")
    if n and m["precision"] < case.get("min_precision", 0.8):
        problems.append("low precision")
    if m["over_budget"] or m["off_host"]:
        problems.append("bad results")
    if case.get("home_station") and n and m["timed"] < case.get("min_timed", 0):
        problems.append("few train times")
    if out["failed"]:
        problems.append("shop failed")
    m["problems"] = problems
    return m


async def main(filters: list[str]) -> int:
    cases = [c for c in json.loads(CASES.read_text()) if not filters or any(f in c["id"] for f in filters)]
    rows = []
    for case in cases:  # one at a time: be gentle with the shops
        args = {k: case[k] for k in ("keywords", "max_price", "chains", "home_station") if k in case}
        start = time.monotonic()
        out = await search_shops(**args)
        rows.append((case["id"], score(case, out, time.monotonic() - start)))

    print(f"{'case':26} {'n':>3} {'prec':>5} {'timed':>5} {'over$':>5} {'host':>4} {'sec':>5}  problems")
    for cid, m in rows:
        f = lambda v: "-" if v is None else v
        print(f"{cid:26} {m['results']:>3} {f(m['precision']):>5} {f(m['timed']):>5} {m['over_budget']:>5} "
              f"{m['off_host']:>4} {m['seconds']:>5}  {', '.join(m['problems'] + m['failed']) or 'ok'}")
    precs = [m["precision"] for _, m in rows if m["precision"] is not None]
    bad = sum(1 for _, m in rows if m["problems"])
    print(f"\n{len(rows) - bad}/{len(rows)} cases ok · mean precision {sum(precs) / len(precs):.2f}" if precs else "")
    return 1 if bad else 0


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main(sys.argv[1:])))
