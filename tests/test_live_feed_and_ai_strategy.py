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
