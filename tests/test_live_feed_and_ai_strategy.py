import numpy as np
import pandas as pd
import pytest

from trading_assistant.market_data.feed import LiveFeed
from trading_assistant.portfolio.manager import PortfolioManager, RiskManager
from trading_assistant.strategies.base import BaseStrategy, Signal
from trading_assistant.strategies.implementations import AIStrategy
from trading_assistant.execution.live import LiveTradingEngine


class DummyClassifier:
    def predict_proba(self, X):
        return np.array([[0.2, 0.8]])


class FakeSandboxedExchange:
    """Stands in for an already-configured (e.g. sandbox-mode) ccxt
    instance - used to prove LiveFeed reuses an injected exchange rather
    than building its own bare (always-mainnet) one."""

    def __init__(self):
        self.fetch_ohlcv_calls = 0

    def fetch_ohlcv(self, symbol, timeframe, limit, since=None):
        self.fetch_ohlcv_calls += 1
        return [[1710000000000, 100.0, 102.0, 99.0, 101.0, 1200.0]]


def test_live_feed_uses_injected_exchange_instead_of_building_its_own():
    """Regression: LiveFeed's default (no injected exchange) path builds a
    bare ccxt.binance() that is NEVER sandboxed regardless of config.mode -
    found via an audit showing MarketMonitorAgent/LearningAgent/
    StrategyManager all defaulted to this bare mainnet path even while
    execution correctly ran on testnet."""
    exchange = FakeSandboxedExchange()
    feed = LiveFeed(symbol="BTC/USDT", exchange=exchange)

    result = feed.fetch_ohlcv(limit=1)

    assert exchange.fetch_ohlcv_calls == 1
    assert result.iloc[0]["close"] == 101.0
    assert feed._get_exchange() is exchange  # never replaced with a fresh ccxt instance


def test_live_feed_converts_ohlcv_rows_to_dataframe():
    rows = [[1710000000000, 100.0, 102.0, 99.0, 101.0, 1200.0]]
    feed = LiveFeed(symbol="BTC/USDT", data_source=lambda *args, **kwargs: rows)

    result = feed.fetch_ohlcv(limit=1)

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert result.iloc[0]["close"] == 101.0


def test_ai_strategy_uses_model_probabilities_to_emit_signals():
    strategy = AIStrategy(model=DummyClassifier(), feature_columns=["close"], name="AI")

    signal = strategy.generate_signal(pd.DataFrame({"close": [100.0, 101.0, 102.0]}))

    assert signal == Signal.BUY


class NeutralClassifier:
    def predict_proba(self, X):
        return np.array([[0.49, 0.51]])


def test_ai_strategy_exposes_raw_probability_driving_a_hold():
    """Regression: `confidence` (an ensemble agreement metric) and the raw
    probability the buy/sell thresholds are applied to are NOT the same
    number - a model can report high confidence while still predicting a
    neutral ~0.5. Without exposing this, a HOLD is indistinguishable from
    "no signal at all" vs. "a razor-thin miss of the threshold"."""
    strategy = AIStrategy(model=NeutralClassifier(), feature_columns=["close"], buy_threshold=0.6, sell_threshold=0.4)

    signal = strategy.generate_signal(pd.DataFrame({"close": [100.0, 101.0, 102.0]}))

    assert signal == Signal.HOLD
    assert strategy.last_probability_up == pytest.approx(0.51)


def test_ai_strategy_resets_probability_when_model_is_none():
    strategy = AIStrategy(model=None, feature_columns=["close"])
    strategy.last_probability_up = 0.9  # simulate a stale value from a previous call

    signal = strategy.generate_signal(pd.DataFrame({"close": [100.0]}))

    assert signal == Signal.HOLD
    assert strategy.last_probability_up is None


class AlwaysBuyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="Always Buy")

    def generate_signal(self, data):
        return Signal.BUY


class DummyExchange:
    def __init__(self):
        self.orders = []

    def create_market_buy_order(self, symbol, quantity):
        self.orders.append((symbol, quantity))
        return {"symbol": symbol, "quantity": quantity, "side": "buy"}


def test_live_trading_engine_opens_a_position_from_buy_signal():
    strategy = AlwaysBuyStrategy()
    exchange = DummyExchange()
    portfolio = PortfolioManager(initial_capital=1000)
    risk_manager = RiskManager(max_position_size=0.5)
    engine = LiveTradingEngine(
        strategy=strategy,
        portfolio=portfolio,
        risk_manager=risk_manager,
        exchange=exchange,
        symbol="BTC/USDT",
    )

    frame = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
    engine.run_once(frame)

    assert "BTC/USDT" in portfolio.positions
    assert exchange.orders[0][0] == "BTC/USDT"
    assert exchange.orders[0][1] == pytest.approx(portfolio.positions["BTC/USDT"]["quantity"])


class FakePaginatedSource:
    """Simulates a Binance-like paginated OHLCV source: bars are `bar_ms`
    apart starting at `start_ts_ms`. Honors `since` (bars >= since) and
    `limit`, matching real ccxt semantics closely enough to test pagination."""

    def __init__(self, bar_ms, total_available_bars, start_ts_ms):
        self.bar_ms = bar_ms
        self.total_available_bars = total_available_bars
        self.start_ts_ms = start_ts_ms

    def __call__(self, symbol, timeframe, limit=100, since=None):
        all_timestamps = [self.start_ts_ms + i * self.bar_ms for i in range(self.total_available_bars)]
        if since is None:
            selected = all_timestamps[-limit:]
        else:
            selected = [ts for ts in all_timestamps if ts >= since][:limit]
        return [[ts, 100.0, 101.0, 99.0, 100.0, 10.0] for ts in selected]


def test_fetch_ohlcv_history_stitches_pages_with_no_gaps_or_duplicates():
    bar_ms = 3_600_000  # 1h
    source = FakePaginatedSource(bar_ms=bar_ms, total_available_bars=3000, start_ts_ms=1_700_000_000_000)
    feed = LiveFeed(symbol="BTC/USDT", timeframe="1h", data_source=source)

    result = feed.fetch_ohlcv_history(total_bars=2500, page_size=1000)

    assert len(result) == 2500
    assert result["timestamp"].is_monotonic_increasing
    assert result["timestamp"].duplicated().sum() == 0
    gaps = result["timestamp"].diff().dropna().unique()
    assert len(gaps) == 1  # every consecutive pair is exactly one bar apart - no leakage/overlap from pagination


def test_fetch_ohlcv_history_caps_at_available_history():
    source = FakePaginatedSource(bar_ms=3_600_000, total_available_bars=500, start_ts_ms=1_700_000_000_000)
    feed = LiveFeed(symbol="BTC/USDT", timeframe="1h", data_source=source)

    result = feed.fetch_ohlcv_history(total_bars=2000, page_size=1000)

    assert len(result) == 500  # can't return more than actually exists
