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
browser-use install                 # downloads the Chromium the agent drives

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

## Models and cost

Each check is one agent run of up to `15 + 4 × open_listings` browser steps, each an LLM call. A small, fast model (Haiku / mini / Flash class) is enough. Lower `open_listings` or raise `every` to spend less.

**Ollama is best-effort.** Small local models often get lost on real sites. Use a capable model and expect failures.

## Be a good citizen

This tool is for personal use. It is read-only: it never logs in, buys, or messages anyone. Keep intervals reasonable (minimum 15 minutes is enforced), and you are responsible for following each site's terms.

## Development

```bash
pip install -e '.[dev]'
pytest
```
