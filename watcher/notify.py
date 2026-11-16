import urllib.parse
import urllib.request


def notify(messages: list[str], token: str | None, chat_id: str | None) -> None:
    for text in messages:
        print(text, end="\n\n")
        if not (token and chat_id):
            continue
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}
        ).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception as e:  # alerts must never crash the watcher
            print(f"telegram send failed: {e}")
