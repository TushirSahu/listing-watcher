from watcher.history import History, recap


def test_conversation_roundtrip(tmp_path):
    h = History(tmp_path / "h.db")
    conv = h.new_conversation("Used WH-1000XM5 under ¥25,000 with the case included, on Mercari please thanks")
    h.add_message(conv, "you", "find xm5")
    h.add_message(conv, "agent", "Found 1.", listings=[{"url": "https://x.jp/1", "price": 19000}], steps=20, cost=0.01)
    msgs = h.messages(conv)
    assert [(m["role"], m["text"]) for m in msgs] == [("you", "find xm5"), ("agent", "Found 1.")]
    assert msgs[1]["listings"] == [{"url": "https://x.jp/1", "price": 19000}] and msgs[0]["listings"] == []
    assert msgs[1]["steps"] == 20 and msgs[1]["cost"] == 0.01
    [c] = h.conversations()
    assert c["conv"] == conv and len(c["title"]) <= 60 and c["title"].startswith("Used WH-1000XM5")


def test_persists_and_latest(tmp_path):
    p = tmp_path / "h.db"
    h = History(p)
    a = h.new_conversation("a")
    b = h.new_conversation("b")
    h.add_message(a, "you", "later message")  # touching a makes it the latest
    h2 = History(p)
    assert h2.latest() == a
    assert [c["conv"] for c in h2.conversations()] == [a, b]
    assert h2.exists(b) and not h2.exists("nope")


def test_latest_empty(tmp_path):
    assert History(tmp_path / "h.db").latest() is None


def test_role_is_checked(tmp_path):
    import sqlite3

    import pytest
    h = History(tmp_path / "h.db")
    conv = h.new_conversation("x")
    with pytest.raises(sqlite3.IntegrityError):
        h.add_message(conv, "robot", "hi")


def test_creates_parent_dir(tmp_path):
    History(tmp_path / "deep" / "dir" / "h.db").new_conversation("x")
    assert (tmp_path / "deep" / "dir" / "h.db").exists()


def test_recap():
    msgs = [
        {"role": "you", "text": "find xm5", "listings": []},
        {"role": "agent", "text": "Found 1.", "listings": [{"url": "https://x.jp/1", "title": "XM5", "price": 19000}]},
    ]
    r = recap(msgs)
    assert r.startswith("Earlier in this conversation")
    assert "User: find xm5" in r and "You answered: Found 1." in r
    assert "#1 ¥19,000 XM5 https://x.jp/1" in r


def test_recap_empty():
    assert recap([]) == ""
