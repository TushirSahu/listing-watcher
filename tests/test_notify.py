from urllib.error import URLError

import watcher.notify as notify_mod
from watcher.notify import notify


def test_no_token_prints_only(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    notify(["hello", "world"], None, None)
    assert calls == []
    out = capsys.readouterr().out
    assert "hello" in out and "world" in out


def test_sends_each_message(monkeypatch):
    reqs = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake(req, timeout=None):
        reqs.append((req.full_url, req.data))
        return Resp()

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake)
    notify(["a", "b"], "TOKEN", "42")
    assert len(reqs) == 2
    assert "/botTOKEN/sendMessage" in reqs[0][0]
    assert b"chat_id=42" in reqs[0][1] and b"text=a" in reqs[0][1]


def test_send_error_not_raised(monkeypatch, capsys):
    def boom(*a, **k):
        raise URLError("down")

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", boom)
    notify(["a"], "T", "1")
    assert "telegram send failed" in capsys.readouterr().out
