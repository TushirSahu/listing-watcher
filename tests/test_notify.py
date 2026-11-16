from urllib.error import URLError

import watcher.notify as notify_mod
from watcher.notify import send_message


class Resp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b"{}"


def test_no_token_prints_only_counts_as_delivered(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    assert send_message("hello", None, None) is True
    assert calls == []
    assert "hello" in capsys.readouterr().out


def test_sends_to_telegram(monkeypatch):
    reqs = []

    def fake(req, timeout=None):
        reqs.append((req.full_url, req.data))
        return Resp()

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake)
    assert send_message("a", "TOKEN", "42") is True
    assert len(reqs) == 1
    assert "/botTOKEN/sendMessage" in reqs[0][0]
    assert b"chat_id=42" in reqs[0][1] and b"text=a" in reqs[0][1]


def test_send_error_returns_false_not_raised(monkeypatch, capsys):
    def boom(*a, **k):
        raise URLError("down")

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", boom)
    assert send_message("a", "T", "1") is False
    assert "telegram send failed" in capsys.readouterr().out
