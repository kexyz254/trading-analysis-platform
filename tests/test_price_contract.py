"""
Tests for the contract-synchronized Price Call Engine.

Covers the lifecycle the refactor introduced:
  * WallClockContractProvider derives a clock-aligned contract, locks the
    horizon candle's OPEN as the Price to Beat, and resolves settlement to that
    candle's CLOSE only after the round ends.
  * The engine predicts exactly one call per contract (duplicate prevention),
    rolls over to a new contract at the boundary, settles against the LOCKED
    Price to Beat, and records millisecond timing.
"""

from __future__ import annotations

import pytest

from webapp import price_call as pc
from webapp.price_contract import Contract, WallClockContractProvider


# ------------------------------------------------------------------ fakes
class FakeKlines:
    """Serves horizon candles keyed by open_time_ms. Lets a test place the
    'current' round's candle (open known, close still forming) and past rounds
    (closed) deterministically."""

    def __init__(self):
        self.candles = {}  # (symbol, tf) -> list[dict]

    def set(self, symbol, tf, candles):
        self.candles[(symbol, tf)] = candles

    def fetch(self, symbol, tf, limit):
        return list(self.candles.get((symbol, tf), []))[-limit:]


def _candle(open_ms, open_, close):
    return {"timestamp": open_ms, "open": open_, "high": max(open_, close) + 1,
            "low": min(open_, close) - 1, "close": close, "volume": 10.0}


STEP = 300_000  # 5m in ms


# ------------------------------------------------------------------ provider
def test_provider_locks_open_as_price_to_beat():
    fk = FakeKlines()
    now_ms = 1_700_000_000_000
    open_ms = (now_ms // STEP) * STEP
    fk.set("BTC/USDT", "5m", [_candle(open_ms, 100.0, 100.5)])
    prov = WallClockContractProvider(fk.fetch, now_ms_fn=lambda: now_ms)

    c = prov.current_contract("BTC/USDT", 300, "5m")
    assert c is not None
    assert c.price_to_beat == 100.0            # the OPEN, not the drifting close
    assert c.open_time_ms == open_ms
    assert c.close_time_ms == open_ms + STEP
    assert c.contract_id == f"BTC/USDT|5m|{open_ms}"
    assert c.source == "wallclock"
    assert 0 <= c.lock_latency_ms < STEP       # locked within the round


def test_provider_no_candle_yet_returns_none():
    fk = FakeKlines()  # no candle published for the current round
    prov = WallClockContractProvider(fk.fetch, now_ms_fn=lambda: 1_700_000_000_000)
    assert prov.current_contract("BTC/USDT", 300, "5m") is None


def test_provider_settlement_only_after_close():
    fk = FakeKlines()
    open_ms = (1_700_000_000_000 // STEP) * STEP
    fk.set("BTC/USDT", "5m", [_candle(open_ms, 100.0, 101.0)])

    # Still inside the round -> no settlement price yet.
    prov_open = WallClockContractProvider(fk.fetch, now_ms_fn=lambda: open_ms + 10_000)
    assert prov_open.settlement_price("BTC/USDT", "5m", open_ms, open_ms + STEP) is None

    # After close -> resolves to the candle's CLOSE.
    prov_closed = WallClockContractProvider(fk.fetch, now_ms_fn=lambda: open_ms + STEP + 1_000)
    assert prov_closed.settlement_price("BTC/USDT", "5m", open_ms, open_ms + STEP) == 101.0


def test_provider_records_server_time_best_effort():
    fk = FakeKlines()
    now_ms = 1_700_000_000_000
    open_ms = (now_ms // STEP) * STEP
    fk.set("BTC/USDT", "5m", [_candle(open_ms, 100.0, 100.1)])
    prov = WallClockContractProvider(
        fk.fetch, now_ms_fn=lambda: now_ms, server_time_fetch=lambda: now_ms + 5,
    )
    assert prov.current_contract("BTC/USDT", 300, "5m").server_time_ms == now_ms + 5

    # A raising server-time source must not break contract creation.
    def _boom():
        raise RuntimeError("clock unavailable")
    prov2 = WallClockContractProvider(fk.fetch, now_ms_fn=lambda: now_ms, server_time_fetch=_boom)
    assert prov2.current_contract("BTC/USDT", 300, "5m").server_time_ms is None


# ------------------------------------------------------------------ engine harness
class ManualProvider:
    """A provider the test drives explicitly: it returns whatever contract is
    'current' and resolves settlement from a dict, so rollover and settlement
    timing are fully controlled."""

    def __init__(self):
        self._current = {}       # (symbol, label) -> Contract | None
        self._settle = {}        # contract_id -> price

    def set_current(self, symbol, label, contract):
        self._current[(symbol, label)] = contract

    def set_settlement(self, contract_id, price):
        self._settle[contract_id] = price

    def current_contract(self, symbol, horizon_seconds, horizon_label):
        return self._current.get((symbol, horizon_label))

    def settlement_price(self, symbol, horizon_label, open_time_ms, close_time_ms):
        cid = f"{symbol}|{horizon_label}|{open_time_ms}"
        return self._settle.get(cid)


def _mk_contract(symbol, open_ms, ptb, label="5m", secs=300):
    return Contract(
        symbol=symbol, horizon_label=label, horizon_seconds=secs,
        contract_id=f"{symbol}|{label}|{open_ms}", open_time_ms=open_ms,
        close_time_ms=open_ms + secs * 1000, price_to_beat=ptb,
        locked_at_ms=open_ms + 200, server_time_ms=None, source="test",
    )


@pytest.fixture
def _stub_inference(monkeypatch):
    """Make _predict_for_contract deterministic: always heuristic UP, no
    champion, cheap move estimate."""
    from webapp import data_access as da
    monkeypatch.setattr(da, "fetch_ohlcv",
                        lambda *a, **k: [{"close": 100.0 + i * 0.01} for i in range(60)])
    import webapp.forecast as fc
    monkeypatch.setattr(fc, "get_forecast",
                        lambda *a, **k: {"horizons": [{"probability_up": 0.7, "hit_rate": 0.5}]},
                        raising=False)
    monkeypatch.setattr(pc, "get_champion_prediction", lambda *a, **k: None)


def _fixed_now(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _engine(tmp_path, provider, now_ms, symbols=("BTC/USDT",)):
    book = pc.PriceCallBook(str(tmp_path / "engine.db"))
    engine = pc.PriceCallEngine(
        None, book, provider, symbols=list(symbols), now_fn=lambda: _fixed_now(now_ms),
    )
    return engine, book


def test_engine_predicts_once_per_contract(tmp_path, monkeypatch, _stub_inference):
    monkeypatch.setattr(pc, "HORIZONS", [(300, "5m", "5m")])
    now_ms = 1_700_000_100_000
    open_ms = now_ms // STEP * STEP
    prov = ManualProvider()
    prov.set_current("BTC/USDT", "5m", _mk_contract("BTC/USDT", open_ms, 100.0))
    engine, book = _engine(tmp_path, prov, now_ms)

    engine.tick()
    engine.tick()  # same contract still current -> must NOT create a second call

    open_call = book.open_call("BTC/USDT", "5m")
    assert open_call is not None
    assert open_call["contract_id"] == f"BTC/USDT|5m|{open_ms}"
    assert open_call["price_at_call"] == 100.0        # locked Price to Beat
    assert book.has_contract(f"BTC/USDT|5m|{open_ms}")


def test_engine_rolls_over_and_settles_against_price_to_beat(tmp_path, monkeypatch, _stub_inference):
    monkeypatch.setattr(pc, "HORIZONS", [(300, "5m", "5m")])
    # Tick 1 fires inside contract 1's window.
    now1 = 1_700_000_100_000
    open1 = now1 // STEP * STEP
    prov = ManualProvider()
    prov.set_current("BTC/USDT", "5m", _mk_contract("BTC/USDT", open1, 100.0))
    engine, book = _engine(tmp_path, prov, now1)
    engine.tick()  # predict contract 1
    assert book.settled_calls("BTC/USDT") == []

    # Roll over: contract 1 has just closed (within the grace window), contract
    # 2 is now current. Contract 1 settles ABOVE its Price to Beat (100.0), so
    # the UP call is correct.
    open2 = open1 + STEP
    now2 = open2 + 5_000  # 5s into contract 2 -> contract 1 closed 5s ago
    prov.set_settlement(f"BTC/USDT|5m|{open1}", 101.0)
    prov.set_current("BTC/USDT", "5m", _mk_contract("BTC/USDT", open2, 101.0))
    engine._now = lambda: _fixed_now(now2)

    engine.tick()  # settle contract 1, predict contract 2

    recent = book.recent_settled("BTC/USDT", "5m", limit=5)
    assert len(recent) == 1
    row = recent[0]
    assert row["contract_id"] == f"BTC/USDT|5m|{open1}"
    assert row["actual_price"] == 101.0
    assert row["correct"] == 1                      # UP call, settled above Price to Beat
    assert row["price_at_call"] == 100.0            # scored vs the LOCKED reference
    assert book.open_call("BTC/USDT", "5m")["contract_id"] == f"BTC/USDT|5m|{open2}"


def test_engine_falls_back_to_spot_when_no_settlement_candle(tmp_path, monkeypatch, _stub_inference):
    """If the contract's own closing candle isn't available yet but we're still
    within grace, settlement uses the freshest spot price rather than stalling."""
    monkeypatch.setattr(pc, "HORIZONS", [(300, "5m", "5m")])
    monkeypatch.setattr(pc, "_current_price", lambda cfg, sym: 99.0)  # below Price to Beat
    now1 = 1_700_000_100_000
    open1 = now1 // STEP * STEP
    prov = ManualProvider()
    prov.set_current("BTC/USDT", "5m", _mk_contract("BTC/USDT", open1, 100.0))
    engine, book = _engine(tmp_path, prov, now1)
    engine.tick()

    # Close arrives, but no settlement candle registered -> spot fallback (99.0).
    now2 = open1 + STEP + 5_000
    engine._now = lambda: _fixed_now(now2)
    engine.tick()

    row = book.recent_settled("BTC/USDT", "5m")[0]
    assert row["actual_price"] == 99.0
    assert row["correct"] == 0                      # UP call, settled BELOW Price to Beat


def test_engine_voids_contract_settled_too_late(tmp_path, monkeypatch, _stub_inference):
    monkeypatch.setattr(pc, "HORIZONS", [(300, "5m", "5m")])
    from datetime import timedelta
    now_ms = 1_700_000_100_000
    prov = ManualProvider()
    engine, book = _engine(tmp_path, prov, now_ms)
    # A call whose due_at is well beyond the grace window (process was down).
    stale_due = (_fixed_now(now_ms) - timedelta(seconds=pc.SETTLE_GRACE_SECONDS + 60)).isoformat()
    open_ms = (now_ms // STEP * STEP) - 10 * STEP
    book.create(symbol="BTC/USDT", price_at_call=100.0, direction="UP", probability=0.7,
                hit_rate_at_call=0.5, projected_price=100.5, expected_move_pct=0.005,
                horizon_seconds=300, horizon_label="5m", due_at=stale_due,
                contract_id=f"BTC/USDT|5m|{open_ms}", open_time_ms=open_ms,
                close_time_ms=open_ms + STEP)

    engine.tick()  # settlement phase should VOID it

    assert book.recent_settled("BTC/USDT", "5m") == []
    assert book.open_call("BTC/USDT", "5m") is None  # settled_at set by void
