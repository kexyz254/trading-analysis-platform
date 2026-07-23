from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import sqlite3

from webapp.price_call import (
    ENGINE_VERSION,
    MARKET_SOURCE,
    STREAM_FRESHNESS_MS,
    MarketMicroState,
    PriceCallBook,
    RealTimePriceCallEngine,
    _configured_symbols,
    _handle_stream_message,
)


DEPTH = {"bids": [["100.00", "90"]], "asks": [["100.01", "10"]]}
TICKS = [
    {"price": 99.90 + index * 0.01, "trade_ms": index}
    for index in range(10)
]


def rows(db_path, query, params=()):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params)]


def cast(engine, trade_ms, horizons=("5m", "15m"), price="100.00000000"):
    return engine.cast_aligned_calls(
        "BTC/USDT",
        price,
        trade_ms + 1,
        trade_ms,
        DEPTH,
        TICKS,
        horizons,
    )


def boundary_trade_ms(offset_ms=75):
    value = 1_784_653_531_075
    return (value // 900_000) * 900_000 + offset_ms


def test_calls_align_to_next_utc_boundaries_in_milliseconds(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path, clock_ms=lambda: 1_000)
    day = (1_784_653_531_075 // 86_400_000) * 86_400_000
    trade_ms = day + 20 * 3_600_000 + 6 * 60_000 + 31_076

    assert len(cast(engine, trade_ms)) == 2
    calls = rows(
        db_path,
        "SELECT * FROM price_calls WHERE legacy = 0 ORDER BY horizon_label DESC",
    )
    by_horizon = {call["horizon_label"]: call for call in calls}

    assert by_horizon["5m"]["created_at"] == trade_ms
    assert by_horizon["5m"]["due_at"] == day + 20 * 3_600_000 + 10 * 60_000
    assert by_horizon["15m"]["due_at"] == day + 20 * 3_600_000 + 15 * 60_000
    assert by_horizon["5m"]["cycle_close_ms"] == by_horizon["5m"]["due_at"] - 1
    assert by_horizon["5m"]["entry_price_text"] == "100.00000000"
    assert by_horizon["5m"]["market_source"] == MARKET_SOURCE
    assert by_horizon["5m"]["engine_version"] == ENGINE_VERSION


def test_price_call_database_contexts_explicitly_close_connections(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)

    with engine._connect() as write_conn:
        assert write_conn.execute("SELECT 1").fetchone()[0] == 1
    try:
        write_conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("price-call write connection remained open")

    book = PriceCallBook(db_path)
    with book._connect() as read_conn:
        assert read_conn.execute("SELECT 1").fetchone()[0] == 1
    try:
        read_conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("price-call read connection remained open")


def test_old_relative_timer_rows_are_preserved_but_classified_legacy(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE price_calls (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL,
                horizon_label TEXT NOT NULL, direction TEXT NOT NULL,
                price_to_beat REAL NOT NULL, actual_price REAL,
                correct INTEGER, miss_pct REAL, created_at INTEGER NOT NULL,
                due_at INTEGER NOT NULL, settled_at INTEGER, voided INTEGER,
                prob_up REAL, prob_down REAL, confidence REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO price_calls VALUES (1, 'BTC/USDT', '5m', 'UP', 100, NULL, NULL, 0, 1000, 1300, NULL, 0, .6, .4, .2)"
        )
    RealTimePriceCallEngine(db_path)

    legacy = rows(
        db_path,
        "SELECT id, legacy, evidence_class FROM price_calls WHERE id = 1",
    )[0]
    assert legacy == {
        "id": 1,
        "legacy": 1,
        "evidence_class": "LEGACY_RELATIVE_TIMER",
    }
    assert PriceCallBook(db_path).open_call("BTC/USDT") == []


def test_concurrent_cast_is_unique_per_symbol_horizon_and_cycle(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    trade_ms = boundary_trade_ms()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cast, engine, trade_ms) for _ in range(16)]
        for future in futures:
            future.result()

    calls = rows(db_path, "SELECT * FROM price_calls WHERE legacy = 0")
    positions = rows(db_path, "SELECT * FROM paper_bankroll_positions")
    assert len(calls) == 2
    assert len(positions) == 2
    assert {call["horizon_label"] for call in calls} == {"5m", "15m"}
    assert {Decimal(position["stake_text"]) for position in positions} == {
        Decimal("1.00000000")
    }

    # A later trade in the same Binance cycle cannot create another call or
    # another linked paper position.
    assert cast(engine, trade_ms + 1_000) == []
    assert len(rows(db_path, "SELECT * FROM price_calls WHERE legacy = 0")) == 2
    assert len(rows(db_path, "SELECT * FROM paper_bankroll_positions")) == 2

    report = PriceCallBook(db_path).bankroll_report()["horizons"]
    assert report["5m"]["open_exposure"] == 1.0
    assert report["15m"]["open_exposure"] == 1.0


def test_price_call_stream_tracks_only_one_configured_symbol(monkeypatch):
    class Config:
        symbols = ["BTC/USDT", "ETH/USDT", "BTCUSDT"]

    monkeypatch.delenv("PRICE_CALL_SYMBOL", raising=False)
    assert _configured_symbols(Config()) == ["BTC/USDT"]

    monkeypatch.setenv("PRICE_CALL_SYMBOL", "ETHUSDT")
    assert _configured_symbols(Config()) == ["ETH/USDT"]


def test_official_close_settles_call_and_bankroll_exactly_once(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    trade_ms = boundary_trade_ms()
    call_id = cast(engine, trade_ms, horizons=("5m",))[0]
    call = rows(db_path, "SELECT * FROM price_calls WHERE id = ?", (call_id,))[0]
    due = call["due_at"]

    first = engine.settle_closed_candle(
        "BTC/USDT", "5m", due - 300_000, due - 1,
        "101.00000000", due + 17,
    )
    duplicate = engine.settle_closed_candle(
        "BTC/USDT", "5m", due - 300_000, due - 1,
        "90.00000000", due + 1_000,
    )

    assert first == call_id
    assert duplicate is None
    settled = rows(db_path, "SELECT * FROM price_calls WHERE id = ?", (call_id,))[0]
    ledger = rows(db_path, "SELECT * FROM paper_bankroll_settlements")[0]
    account = rows(
        db_path,
        "SELECT * FROM paper_bankroll_accounts WHERE horizon_label = '5m'",
    )[0]
    assert settled["actual_price_text"] == "101.00000000"
    assert settled["actual_direction"] == "UP"
    assert settled["correct"] == 1
    assert settled["settlement_latency_ms"] == 17
    assert settled["settlement_method"] == "STREAM_KLINE"
    assert Decimal(ledger["stake_text"]) == Decimal("1.00000000")
    assert Decimal(ledger["fee_cost_text"]) == Decimal("0.00201000")
    assert Decimal(ledger["slippage_cost_text"]) == Decimal("0.00040200")
    assert Decimal(ledger["net_pnl_text"]) == Decimal("0.00758800")
    assert Decimal(account["settled_balance_text"]) == Decimal("50.00758800")
    assert len(rows(db_path, "SELECT * FROM paper_bankroll_settlements")) == 1

    next_call_id = cast(engine, due + 1, horizons=("5m",))[0]
    next_position = rows(
        db_path,
        "SELECT * FROM paper_bankroll_positions WHERE call_id = ?",
        (next_call_id,),
    )[0]
    assert Decimal(next_position["stake_text"]) == Decimal("1.00000000")


def test_metrics_separate_forecast_financial_and_evidence_quality(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    trade_ms = boundary_trade_ms()
    call_id = cast(engine, trade_ms, horizons=("5m",))[0]
    due = rows(db_path, "SELECT due_at FROM price_calls WHERE id = ?", (call_id,))[0]["due_at"]
    engine.settle_closed_candle(
        "BTC/USDT", "5m", due - 300_000, due - 1,
        "101.00000000", due + 25,
    )

    book = PriceCallBook(db_path)
    metrics = book.stats("BTC/USDT")["by_horizon"]["5m"]
    bankroll = book.bankroll_report()["horizons"]["5m"]
    assert metrics["resolved"] == 1
    assert metrics["sample_status"] == "INSUFFICIENT_SAMPLE"
    assert metrics["brier_score"] is not None
    assert metrics["wilson_95_low"] is not None
    assert metrics["median_settlement_latency_ms"] == 25
    assert bankroll["settlements"] == 1
    assert bankroll["open_exposure"] == 0.0
    assert bankroll["net_pnl"] == 0.007588


def test_missing_official_close_voids_without_changing_balance(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    trade_ms = boundary_trade_ms()
    call_id = cast(engine, trade_ms, horizons=("5m",))[0]
    due = rows(db_path, "SELECT due_at FROM price_calls WHERE id = ?", (call_id,))[0]["due_at"]

    assert engine.void_stale_calls("BTC/USDT", due + 15_001) == 1
    call = rows(db_path, "SELECT * FROM price_calls WHERE id = ?", (call_id,))[0]
    position = rows(db_path, "SELECT * FROM paper_bankroll_positions WHERE call_id = ?", (call_id,))[0]
    account = rows(
        db_path,
        "SELECT settled_balance_text FROM paper_bankroll_accounts WHERE horizon_label = '5m'",
    )[0]
    assert call["voided"] == 1
    assert call["void_reason"] == "MISSING_OFFICIAL_CLOSE"
    assert position["status"] == "VOID"
    assert Decimal(account["settled_balance_text"]) == Decimal("50.00")


def test_stream_ignores_open_kline_and_uses_closed_kline(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    trade_ms = boundary_trade_ms()
    call_id = cast(engine, trade_ms, horizons=("5m",))[0]
    due = rows(db_path, "SELECT due_at FROM price_calls WHERE id = ?", (call_id,))[0]["due_at"]
    base = {
        "stream": "btcusdt@kline_5m",
        "data": {
            "e": "kline", "E": due + 10, "s": "BTCUSDT",
            "k": {"i": "5m", "t": due - 300_000, "T": due - 1, "c": "101.0", "x": False},
        },
    }
    _handle_stream_message(engine, {"BTCUSDT": "BTC/USDT"}, base)
    assert rows(db_path, "SELECT settled_at FROM price_calls WHERE id = ?", (call_id,))[0]["settled_at"] is None
    base["data"]["k"]["x"] = True
    _handle_stream_message(engine, {"BTCUSDT": "BTC/USDT"}, base)
    assert rows(db_path, "SELECT settled_at FROM price_calls WHERE id = ?", (call_id,))[0]["settled_at"] == due + 10


def test_first_trade_before_closed_kline_does_not_skip_next_cycle(tmp_path):
    db_path = str(tmp_path / "analytics.db")
    engine = RealTimePriceCallEngine(db_path)
    symbol = "RACE/USDT"
    start = 1_784_653_531_000
    for index in range(10):
        engine.handle_agg_trade(
            symbol, f"{100 + index / 100:.8f}", start + index, start + index
        )
    old = rows(
        db_path,
        "SELECT * FROM price_calls WHERE symbol = ? AND horizon_label = '5m' AND legacy = 0",
        (symbol,),
    )[0]
    due = old["due_at"]

    # Binance aggregate trade can arrive milliseconds before the x=true kline.
    assert engine.handle_agg_trade(symbol, "101.00000000", due + 1, due + 1) == []
    engine.settle_closed_candle(
        symbol, "5m", due - 300_000, due - 1, "101.00000000", due + 2
    )
    inserted = engine.handle_agg_trade(
        symbol, "101.01000000", due + 3, due + 3
    )

    assert inserted
    active = rows(
        db_path,
        "SELECT * FROM price_calls WHERE symbol = ? AND horizon_label = '5m' AND legacy = 0 AND settled_at IS NULL AND voided = 0",
        (symbol,),
    )
    assert len(active) == 1
    assert active[0]["due_at"] == due + 300_000


def test_market_stream_status_requires_a_recent_binance_message():
    state = MarketMicroState()
    state.set_stream_status(True)
    state.update_trade("BTC/USDT", "66344.51000000", 1_000_000, 999_999)

    live = state.snapshot("BTC/USDT", now_ms=1_000_000 + STREAM_FRESHNESS_MS)
    stale = state.snapshot("BTC/USDT", now_ms=1_000_001 + STREAM_FRESHNESS_MS)
    state.set_stream_status(False, "test disconnect")
    offline = state.snapshot("BTC/USDT", now_ms=1_000_002 + STREAM_FRESHNESS_MS)

    assert live["stream"]["status"] == "LIVE"
    assert live["stream"]["fresh"] is True
    assert stale["stream"]["status"] == "STALE"
    assert stale["stream"]["fresh"] is False
    assert offline["stream"]["status"] == "OFFLINE"
    assert offline["stream"]["last_error"] == "test disconnect"
