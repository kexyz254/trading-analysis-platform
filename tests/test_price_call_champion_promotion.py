"""
Promotion -> champion tests for the price-call engine.

Verifies the full contract of Step 5:
  * get_champion_prediction returns None when no price_call candidate is a
    promoted tournament winner (heuristic fallback territory).
  * When the tournament journal records price_call_{horizon} as a regime
    winner, get_champion_prediction trains the candidate's calibrated models
    on live 15m data and returns a valid probability for THAT horizon only.
  * _make_call marks the stored call champion_backed=1 when a champion exists,
    and 0 when it falls back to the heuristic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_assistant.persistence.journal import Journal
from webapp import data_access as da
from webapp import price_call as pc


# ------------------------------------------------------------------ helpers
def _synth_15m_raw(n: int = 700, seed: int = 5):
    """da.fetch_ohlcv-shaped rows (ms timestamps) of synthetic 15m candles."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    ms = (idx.astype("int64") // 10**6).tolist()
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, size=n)))
    rows = []
    prev = close[0]
    for i in range(n):
        c = float(close[i])
        rows.append({
            "timestamp": ms[i],
            "open": prev,
            "high": c * (1 + abs(rng.normal(0, 0.0015))),
            "low": c * (1 - abs(rng.normal(0, 0.0015))),
            "close": c,
            "volume": float(rng.uniform(10, 100)),
        })
        prev = c
    return rows


def _journal_with_winner(tmp_path, winner_by_regime):
    journal = Journal(str(tmp_path / "trading.db"))  # real, initialized db
    journal.record_tournament_event(
        symbol="BTC/USDT",
        winners={r: {"winner": w, "reason": "test"} for r, w in winner_by_regime.items()},
        report={"candidates": []},
    )
    return journal


# ------------------------------------------------------------------ tests
def test_no_event_returns_none(tmp_path):
    journal = Journal(str(tmp_path / "trading.db"))
    assert pc.get_champion_prediction(None, "BTC/USDT", "5m", journal=journal) is None


def test_no_price_call_winner_returns_none(tmp_path):
    journal = _journal_with_winner(tmp_path, {"trending_up": "time_series_momentum"})
    assert pc.get_champion_prediction(None, "BTC/USDT", "5m", journal=journal) is None


def test_promoted_champion_returns_prediction(tmp_path, monkeypatch):
    journal = _journal_with_winner(tmp_path, {"trending_up": "price_call_5m"})
    monkeypatch.setattr(da, "fetch_ohlcv", lambda *a, **k: _synth_15m_raw())

    out = pc.get_champion_prediction(None, "BTC/USDT", "5m", journal=journal)
    assert out is not None
    assert out["champion_name"] == "price_call_5m"
    assert 0.0 <= out["probability_up"] <= 1.0
    assert out["direction"] in ("UP", "DOWN")
    assert 0.5 <= out["confidence"] <= 1.0
    assert out["trained_rows"] > 0


def test_champion_is_horizon_specific(tmp_path, monkeypatch):
    # Only the 5m candidate is promoted; the 15m horizon must NOT be
    # champion-backed just because its sibling was promoted.
    journal = _journal_with_winner(tmp_path, {"trending_up": "price_call_5m"})
    monkeypatch.setattr(da, "fetch_ohlcv", lambda *a, **k: _synth_15m_raw())

    assert pc.get_champion_prediction(None, "BTC/USDT", "5m", journal=journal) is not None
    assert pc.get_champion_prediction(None, "BTC/USDT", "15m", journal=journal) is None


def _contract(symbol, open_ms=1_700_000_000_000):
    from webapp.price_contract import Contract
    return Contract(
        symbol=symbol, horizon_label="5m", horizon_seconds=300,
        contract_id=f"{symbol}|5m|{open_ms}", open_time_ms=open_ms,
        close_time_ms=open_ms + 300_000, price_to_beat=100.0,
        locked_at_ms=open_ms + 500, server_time_ms=open_ms + 480, source="wallclock",
    )


def test_prediction_marks_champion_backed_and_uses_price_to_beat(tmp_path, monkeypatch):
    book = pc.PriceCallBook(str(tmp_path / "analytics.db"))

    # Heuristic pieces stubbed so the test is deterministic and fast.
    monkeypatch.setattr(da, "fetch_ohlcv",
                        lambda *a, **k: [{"close": 100.0 + i * 0.01} for i in range(60)])
    import webapp.forecast as fc
    monkeypatch.setattr(
        fc, "get_forecast",
        lambda *a, **k: {"horizons": [{"probability_up": 0.4, "hit_rate": 0.5}]},
        raising=False,
    )

    # 1) Champion present -> champion_backed call, champion's direction wins,
    #    and the recorded reference IS the contract's locked Price to Beat.
    monkeypatch.setattr(
        pc, "get_champion_prediction",
        lambda *a, **k: {"direction": "UP", "confidence": 0.9,
                         "probability_up": 0.9, "champion_name": "price_call_5m",
                         "trained_rows": 300},
    )
    c = _contract("BTC/USDT")
    call_id = pc._predict_for_contract(None, book, c, "5m")
    row = book.open_call("BTC/USDT", "5m")
    assert row["id"] == call_id
    assert row["champion_backed"] == 1
    assert row["direction"] == "UP"  # champion overrides the heuristic's DOWN lean
    assert abs(row["probability"] - 0.9) < 1e-9
    assert abs(row["price_at_call"] - c.price_to_beat) < 1e-9
    assert row["contract_id"] == c.contract_id
    assert row["open_time_ms"] == c.open_time_ms and row["close_time_ms"] == c.close_time_ms

    # 2) No champion -> heuristic fallback, champion_backed=0.
    monkeypatch.setattr(pc, "get_champion_prediction", lambda *a, **k: None)
    pc._predict_for_contract(None, book, _contract("ETH/USDT"), "5m")
    row2 = book.open_call("ETH/USDT", "5m")
    assert row2["champion_backed"] == 0
    assert row2["direction"] == "DOWN"  # p_up=0.4 -> DOWN


def test_duplicate_contract_prediction_prevented(tmp_path, monkeypatch):
    """A second prediction for the same contract_id must be a no-op - the
    engine (and the UNIQUE index) enforce exactly one call per round."""
    book = pc.PriceCallBook(str(tmp_path / "analytics.db"))
    monkeypatch.setattr(da, "fetch_ohlcv",
                        lambda *a, **k: [{"close": 100.0 + i * 0.01} for i in range(60)])
    import webapp.forecast as fc
    monkeypatch.setattr(fc, "get_forecast",
                        lambda *a, **k: {"horizons": [{"probability_up": 0.6, "hit_rate": 0.5}]},
                        raising=False)
    monkeypatch.setattr(pc, "get_champion_prediction", lambda *a, **k: None)

    c = _contract("BTC/USDT")
    first = pc._predict_for_contract(None, book, c, "5m")
    assert first is not None
    assert book.has_contract(c.contract_id)
    second = pc._predict_for_contract(None, book, c, "5m")  # same contract again
    assert second is None
    # exactly one row for the contract
    assert len(book.settled_calls("BTC/USDT")) == 0  # none settled yet
    assert book.open_call("BTC/USDT", "5m")["id"] == first
