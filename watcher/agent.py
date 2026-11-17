import contextlib
from collections.abc import Mapping

from browser_use import Agent, Browser, ChatAnthropic, ChatGoogle, ChatOpenAI

from watcher.models import Reply


class ConfigError(Exception):
    pass


class RetryingGoogle(ChatGoogle):
    """ChatGoogle that retries 429/503 with backoff. Gemini's free tier allows ~15 requests/min, and an
    agent run bursts past that; browser-use's ChatGoogle sets no retries, so one 429 kills the run."""

    def pydantic_model(self):
        from google.genai import types
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        retry = types.HttpRetryOptions(attempts=8, initial_delay=5, max_delay=60, http_status_codes=[429, 503])
        return GoogleModel(self.model, provider=GoogleProvider(api_key=self.api_key, retry_options=retry))


PROVIDERS = {
    "anthropic": (ChatAnthropic, "ANTHROPIC_API_KEY"),
    "openai": (ChatOpenAI, "OPENAI_API_KEY"),
    "google": (RetryingGoogle, "GOOGLE_API_KEY"),
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


SYSTEM_RULES = "\n".join([
    "You are a shopping and listings assistant. The user chats with you; each message is a request or a follow-up "
    "about earlier results (\"#2\" means listing 2 of your last list).",
    "Sites: use the site the user names. If none is named, pick suitable ones: Mercari, Yahoo Auctions or Rakuma for "
    "used goods; Amazon.co.jp for new items; SUUMO for rentals. Read search results pages directly instead of "
    "fighting filter buttons; open the most promising listings to check condition and details.",
    "Rules: this is read-only. do NOT log in, buy, message sellers, or click purchase buttons.",
    "Skip listings marked sold, reserved, or unavailable (SOLD / 売り切れ / 取引中).",
    "price = what the buyer actually pays per period, as an integer in yen: include mandatory recurring fees "
    "(rent + 管理費/共益費 for rentals; item + shipping if charged). One-time costs (敷金/礼金, agency fee) are not "
    "price: mention them in why_matches. Convert 万円 to yen (8.35万円 = 83500).",
    "item_key identifies the physical item regardless of link: apartments building + floor + layout + size "
    "(e.g. 'Maison Suita 3F 1K 25m2'); marketplace items seller + exact title. null if unsure.",
    "If a requirement is simply not stated on a listing, do not reject it for that: set matches true if everything "
    "else fits and say what is unconfirmed in why_matches. Reject only when the listing contradicts the request.",
    "url must be the listing's own detail page as a full https link; never javascript: or # links. "
    "image_url = the listing's main photo as a full https link (its <img> src), or null.",
    "Reply with a short answer in English. Put listings in listings only when you are showing results "
    "(matches true for ones that fit); for questions about one listing, answer in answer and leave listings empty.",
])

MAX_STEPS = 40


class ChatSession:
    """One conversation: one browser-use Agent and one kept-alive browser; follow-ups continue the same Agent."""

    def __init__(self, llm: object, headless: bool = True, max_context_chars: int | None = None):
        self.llm, self.headless, self.max_context_chars = llm, headless, max_context_chars
        self.agent = self.browser = None

    async def ask(self, text: str) -> Reply:
        try:
            if self.agent is None:
                self.browser = Browser("chromium", headless=self.headless, keep_alive=True)
                self.agent = Agent(
                    task=text,
                    llm=self.llm,
                    browser=self.browser,
                    output_model_schema=Reply,
                    extend_system_message=SYSTEM_RULES,
                    judge=False,  # skip the extra judge LLM call
                    max_context_chars=self.max_context_chars,  # cap tokens per step (free-tier limits)
                )
            else:
                self.agent.add_new_task(text)
            history = await self.agent.run(max_steps=MAX_STEPS)
            out = history.structured_output
            if out is None:
                # browser-use 1.0 reports bad keys, timeouts, step limits via status/error instead of raising
                reason = history.error or history.final_result() or "no structured result"
                return Reply(answer=f"error: agent run {history.status}: {reason}"[:400])
            return Reply.model_validate(out)
        except Exception as e:  # a failed turn is reported; the conversation goes on
            return Reply(answer=f"error: {type(e).__name__}: {e}"[:400])

    async def close(self) -> None:
        if self.browser is not None:
            with contextlib.suppress(Exception):
                await self.browser.kill()
        self.agent = self.browser = None
