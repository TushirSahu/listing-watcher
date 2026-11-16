import urllib.parse
import urllib.request


def send_message(text: str, token: str | None, chat_id: str | None) -> bool:
    """Print the alert; also send it to Telegram when configured. Returns True if delivered."""
    print(text, end="\n\n")
    if not (token and chat_id):
        return True  # console-only mode: printing is the delivery
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}
    ).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as e:  # alerts must never crash the watcher; caller retries next run
        print(f"telegram send failed: {e}")
        return False
