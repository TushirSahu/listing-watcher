# listing-watcher

A browser agent that watches shopping and listing sites for you. Describe what you want in plain words. On a schedule an AI opens a real browser, searches the site, opens the most promising listings to check condition and details, and pings you on Telegram when something new matches or a price drops.

Built on [browser-use](https://github.com/browser-use/browser-use). Bring your own model: Anthropic, OpenAI, Google, or a local Ollama model.

```
[headphones] new: Sony WH-1000XM5 美品 ケース付き - ¥23,500
good condition per description, case included, under budget
https://jp.mercari.com/item/m12345678901
```

## Setup

Requires Python 3.11+.

```bash
git clone <this repo> && cd listing-watcher
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
# The agent uses your installed Chrome/Chromium/Edge with a fresh, empty profile
# (never your logged-in sessions). No Chrome installed? Get Chromium:
#   pip install playwright && playwright install chromium

cp .env.example .env                # choose a provider, paste your API key
cp watches.example.yaml watches.yaml  # describe what to watch
```

### Telegram alerts (optional)

1. In Telegram, message **@BotFather** with `/newbot` and copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<token>/getUpdates` and copy `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`.

If you skip this, alerts print to the console.

## Use

```bash
listing-watcher check headphones --show-browser   # test one watch: watch the browser, print what it found
listing-watcher run                               # keep running; check each watch on its schedule
```

`check` never sends alerts and never touches the agent's memory, so you can tune a watch freely.

On the first `run` of a new watch, current matches are recorded quietly and you get **one** summary message. After that you get one message per new match or price drop. If a check fails (captcha, site changed, agent got lost) you get one alert, then one "working again" when it recovers, with no repeat alerts in between.

## Watch fields (`watches.yaml`)

| field | required | default | meaning |
|---|---|---|---|
| `name` | yes | | short id, used in alerts and `check` |
| `site` | yes | | start URL, e.g. `https://jp.mercari.com` |
| `want` | yes | | plain-language request, including fuzzy criteria ("good condition, with case") |
| `max_price` | no | none | yen; listings above it are never alerted |
| `every` | no | `2h` | `30m` / `2h` / `1d`; minimum `15m` |
| `max_results` | no | `10` | search results the agent reads per check |
| `open_listings` | no | `5` | top candidates the agent opens to verify details; `0` = search page only (alerts marked *unverified*) |

## Apartment hunting

`watches.example.yaml` includes a SUUMO watch (`apartment`): 1K/1LDK rentals in Suita, Osaka, near a station, foreigner-friendly, up to ¥70,000/month. Change the `want` text to your area and needs and set `max_price` to your budget. Try it with `listing-watcher check apartment`.

How it handles rentals (and any other site):

- **Price is the real monthly cost:** rent + management fee (管理費/共益費). One-time costs (deposit, key money, agency fee) are listed in the alert text instead. For shop items, price includes shipping when it's charged.
- **The same flat from several agencies alerts once.** The agent reports a short identity for each item (building, floor, layout, size), and repeats are recognized even under different links. If another agency lists the same flat for less, you get a price-drop alert.

## Models and cost

Each check is one agent run of up to `15 + 4 × open_listings` browser steps, each an LLM call. A small, fast model (Haiku / mini / Flash class) is enough. Lower `open_listings` or raise `every` to spend less.

**Free option: Google Gemini.** Get a key at aistudio.google.com, then set `LLM_PROVIDER=google`, `LLM_MODEL=gemini-flash-lite-latest`. The free tier allows only a few requests per minute per model (5/min on `gemini-2.5-flash` at time of writing), and the agent makes one request per browser step, so non-lite models often fail with `429 quota exceeded`. Lite models work, and a check takes a few minutes. Big pages (SUUMO) can also hit the tokens-per-minute limit: set `LLM_MAX_CONTEXT_CHARS=60000` in `.env`.

**Ollama is best-effort.** Small local models often get lost on real sites. Use a capable model and expect failures.

## Be a good citizen

This tool is for personal use. It is read-only: it never logs in, buys, or messages anyone. Keep intervals reasonable (minimum 15 minutes is enforced), and you are responsible for following each site's terms.

## Development

```bash
pip install -e '.[dev]'
pytest
```
