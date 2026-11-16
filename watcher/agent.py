import contextlib
from collections.abc import Mapping

from browser_use import Agent, Browser, ChatAnthropic, ChatGoogle, ChatOpenAI

from watcher.config import ConfigError, Watch
from watcher.models import RunResult, failed_result

PROVIDERS = {
    "anthropic": (ChatAnthropic, "ANTHROPIC_API_KEY"),
    "openai": (ChatOpenAI, "OPENAI_API_KEY"),
    "google": (ChatGoogle, "GOOGLE_API_KEY"),
    "ollama": (ChatOpenAI, None),
}


def make_llm(provider: str, model: str, env: Mapping[str, str]) -> object:
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)} (got {provider!r})")
    if not model:
        raise ConfigError("LLM_MODEL is empty")
    cls, key_var = PROVIDERS[provider]
    if key_var:
        if not env.get(key_var):
            raise ConfigError(f"{key_var} is not set (needed for LLM_PROVIDER={provider})")
        return cls(model=model, api_key=env[key_var])
    # browser-use 1.0 removed ChatOllama; Ollama speaks the OpenAI API.
    host = env.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    return ChatOpenAI(model=model, base_url=f"{host}/v1", api_key="ollama")


def max_steps(watch: Watch) -> int:
    return 15 + 4 * watch.open_listings


def build_task(watch: Watch) -> str:
    lines = [
        f"Go to {watch.site} and search for listings matching this request:",
        f'"{watch.want}"',
    ]
    if watch.max_price is not None:
        lines.append(f"Budget: at most ¥{watch.max_price:,}. Ignore listings above it.")
    lines.append(f"Prefer newest listings first if the site allows sorting. read at most {watch.max_results} results.")
    if watch.open_listings > 0:
        lines.append(
            f"Then open the top {watch.open_listings} candidate listings on their own page to check details "
            "(condition, description, what is included) and set verified to true for those you opened."
        )
    else:
        lines.append("Judge from the search results page only: do not open listing pages; set verified to false.")
    lines += [
        "Skip listings marked sold, reserved, or unavailable (e.g. SOLD / 売り切れ / 取引中): never report them as matches.",
        "Rules: this is read-only. do NOT log in, buy, message, or click purchase buttons.",
        "If you hit a captcha, login wall, or cannot find a search, set blocked_or_lost true and explain in note.",
        "Price means what the buyer actually pays per period, as an integer in yen: include mandatory recurring fees "
        "(rent + 管理費/共益費 for rentals; item + shipping if shipping is charged). One-time costs (敷金/礼金, agency fee) "
        "are NOT part of price: mention them in why_matches instead.",
        "item_key identifies the physical item regardless of link, so the same thing listed twice is recognized: "
        "for apartments building name + floor + layout + size (e.g. 'Maison Suita 3F 1K 25m2'); for marketplace items "
        "seller + exact title. Use null if unsure.",
        "Report every listing you looked at. For each: full url, title, item_key, price (null if unknown), "
        "matches (true only if it fits the request and budget), verified, and why_matches (one short line, in English).",
        "Set searched true once you have run a search, and listings_seen to how many results you read.",
    ]
    return "\n".join(lines)


async def run_watch(watch: Watch, llm: object, headless: bool = True) -> RunResult:
    browser = None
    try:
        browser = Browser("chromium", headless=headless)
        agent = Agent(
            task=build_task(watch),
            llm=llm,
            browser=browser,
            output_model_schema=RunResult,
            judge=False,  # skip extra judge LLM call: we judge listings ourselves
        )
        history = await agent.run(max_steps=max_steps(watch))
        out = history.structured_output
        if out is None:
            # browser-use 1.0 reports bad keys, timeouts, step limits via status/error instead of raising
            status = getattr(history, "status", None) or "unknown"
            reason = getattr(history, "error", None) or (history.final_result() if hasattr(history, "final_result") else None)
            return failed_result(f"agent run {status}: {reason or 'no structured result'}"[:300])
        return out if isinstance(out, RunResult) else RunResult.model_validate(out)
    except Exception as e:  # never raise: a broken run is reported, not fatal
        return failed_result(f"{type(e).__name__}: {e}"[:300])
    finally:
        with contextlib.suppress(Exception):  # browser may be None if launch failed
            await browser.kill()
