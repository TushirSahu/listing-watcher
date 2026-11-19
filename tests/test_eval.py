from evals.run import score


def r(title, price=1000, url="https://netmall.hardoff.co.jp/product/1/", why=""):
    return {"title": title, "price": price, "url": url, "why_matches": why}


def test_score_counts_precision_budget_host_and_train_times():
    case = {"keywords": "WH-1000XM5", "max_price": 25000, "home_station": "吹田", "title_has": ["1000xm5"],
            "min_timed": 0.5}
    out = {"failed": [], "results": [r("SONY WH-1000XM5"), r("SONY WH-1000 XM5", why="8 min from 吹田"),
                                     r("WH-1000XM4", 30000), r("XM5", url="https://evil.example/1")]}
    m = score(case, out, 1.0)
    assert (m["results"], m["precision"], m["over_budget"], m["off_host"], m["timed"]) == (4, 0.5, 1, 1, 0.25)
    assert m["problems"] == ["low precision", "bad results", "few train times"]


def test_score_empty_case_ok():
    m = score({"keywords": "x", "min_results": 0, "max_results": 0}, {"results": [], "failed": []}, 0.1)
    assert m["problems"] == [] and m["precision"] is None
