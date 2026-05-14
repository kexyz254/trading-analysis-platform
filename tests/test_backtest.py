import pytest
from trading_assistant.core.ohlc import Bar
from trading_assistant.backtesting.engine import BacktestEngine
from trading_assistant.strategies.base import BaseStrategy, Signal
from datetime import datetime

class BuyStrategy(BaseStrategy):
    def generate_signal(self, data):
        return Signal.BUY

def test_backtest_handles_zero_price():
    bars = [
        Bar(datetime.now(), 0, 0, 0, 0, 1000),
        Bar(datetime.now(), 100, 100, 100, 100, 1000),
    ]
    engine = BacktestEngine(10000)
    result = engine.run(bars, BuyStrategy("test"))
    assert result['total_trades'] == 1  # should skip zero price, buy at 100
