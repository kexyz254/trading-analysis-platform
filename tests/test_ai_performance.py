"""
Tests for the AI performance track record (cumulative accuracy / edge path).

All figures come from REAL settled calls in a PriceCallBook - we create calls,
settle them against known prices, and assert the derived hit rate, cumulative
edge, champion-vs-heuristic split, and path shape.
"""

from __future__ import annotations

from webapp import price_call as pc
from webapp import server


def _make_settled(book, *, symbol, price0, price1, direction, champion_backed, horizon="5m"):
    call_id = book.create(
        symbol=symbol, price_at_call=price0, direction=direction,
        probability=0.7, hit_rate_at_call=0.5, projected_price=price0,
        expected_move_pct=0.001, horizon_seconds=300, horizon_label=horizon,
        champion_backed=champion_backed,
    )
    book.settle(call_id, price1)
    return call_id


def test_performance_summary_empty(tmp_path):
    book = pc.PriceCallBook(str(tmp_path / "a.db"))
    out = pc.performance_summary(book, "BTC/USDT")
    assert out["n"] == 0
    assert out["hit_rate"] is None
    assert out["path"] == []


def test_performance_summary_computes_real_track_record(tmp_path):
    book = pc.PriceCallBook(str(tmp_path / "a.db"))
    # 1) champion UP, went up  -> correct,   +1%
    _make_settled(book, symbol="BTC/USDT", price0=100.0, price1=101.0, direction="UP", champion_backed=True)
    # 2) heuristic DOWN, went down -> correct, +1% (in predicted direction)
    _make_settled(book, symbol="BTC/USDT", price0=100.0, price1=99.0, direction="DOWN", champion_backed=False)
    # 3) heuristic UP, went down -> wrong,   -1%
    _make_settled(book, symbol="BTC/USDT", price0=100.0, price1=99.0, direction="UP", champion_backed=False)

    out = pc.performance_summary(book, "BTC/USDT")
    assert out["n"] == 3
    assert abs(out["hit_rate"] - 2 / 3) < 1e-9
    assert abs(out["cum_edge_pct"] - 1.0) < 1e-6  # +1 +1 -1 = +1%
    assert out["champion"]["n"] == 1 and abs(out["champion"]["hit_rate"] - 1.0) < 1e-9
    assert out["heuristic"]["n"] == 2 and abs(out["heuristic"]["hit_rate"] - 0.5) < 1e-9

    path = out["path"]
    assert [p["n"] for p in path] == [1, 2, 3]
    assert abs(path[-1]["accuracy"] - 2 / 3) < 1e-9
    assert path[0]["champion_backed"] is True
    # accuracy is monotonic in cumulative hits/total, bounded [0,1]
    for p in path:
        assert 0.0 <= p["accuracy"] <= 1.0


def test_ai_performance_endpoint(tmp_path, monkeypatch):
    book = pc.PriceCallBook(str(tmp_path / "a.db"))
    _make_settled(book, symbol="ETH/USDT", price0=50.0, price1=51.0, direction="UP", champion_backed=True)
    monkeypatch.setattr(pc, "get_book", lambda *a, **k: book)

    payload = server.pro_ai_performance("ETH/USDT")
    assert payload["symbol"] == "ETH/USDT"
    assert payload["n"] == 1
    assert payload["hit_rate"] == 1.0
    assert payload["champion"]["n"] == 1
    assert len(payload["path"]) == 1
