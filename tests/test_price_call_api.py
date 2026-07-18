"""
API test: /api/pro/price-call surfaces champion_backed per horizon.

The endpoint reads the dashboard-owned analytics db via get_book(); we point
it at a temp book carrying one champion-backed open call and one heuristic
one, then call the endpoint function and assert the flag is reported
correctly for each horizon.
"""

from __future__ import annotations

import pandas as pd  # noqa: F401 - ensures pandas import order matches app

from webapp import price_call as pc
from webapp import server


def _seed_book(tmp_path):
    book = pc.PriceCallBook(str(tmp_path / "analytics.db"))
    # 5m call made by a promoted champion.
    book.create(
        symbol="BTC/USDT", price_at_call=100.0, direction="UP",
        probability=0.82, hit_rate_at_call=0.6, projected_price=100.1,
        expected_move_pct=0.001, horizon_seconds=300, horizon_label="5m",
        champion_backed=True,
    )
    # 15m call made by the heuristic fallback.
    book.create(
        symbol="BTC/USDT", price_at_call=100.0, direction="DOWN",
        probability=0.55, hit_rate_at_call=0.5, projected_price=99.9,
        expected_move_pct=0.001, horizon_seconds=900, horizon_label="15m",
        champion_backed=False,
    )
    return book


def test_price_call_endpoint_reports_champion_backed(tmp_path, monkeypatch):
    book = _seed_book(tmp_path)
    monkeypatch.setattr(pc, "get_book", lambda *a, **k: book)

    payload = server.pro_price_calls("BTC/USDT")
    by_horizon = {h["horizon"]: h for h in payload["horizons"]}

    assert by_horizon["5m"]["champion_backed"] is True
    assert by_horizon["5m"]["open_call"]["champion_backed"] == 1
    assert by_horizon["15m"]["champion_backed"] is False
    assert by_horizon["15m"]["open_call"]["champion_backed"] == 0


def test_price_call_endpoint_champion_backed_false_when_no_open_call(tmp_path, monkeypatch):
    # Empty book -> no open call -> champion_backed defaults to False, no crash.
    book = pc.PriceCallBook(str(tmp_path / "empty.db"))
    monkeypatch.setattr(pc, "get_book", lambda *a, **k: book)

    payload = server.pro_price_calls("ETH/USDT")
    for h in payload["horizons"]:
        assert h["champion_backed"] is False
        assert h["open_call"] is None
