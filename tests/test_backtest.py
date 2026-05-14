import pytest
from trading_assistant.core.ohlc import Bar
from trading_assistant.backtesting.engine import BacktestEngine
from trading_assistant.strategies.base import BaseStrategy, Signal
from datetime import datetime

class SimpleStrategy(BaseStrategy):
    def generate_signal(self, data):
        if data['price'] > 105:
            return Signal.SELL
        elif data['price'] < 95:
            return Signal.BUY
        return Signal.HOLD

def test_multiple_trades_maintains_capital():
    bars = [
        Bar(datetime.now(), 100, 100, 100, 95, 1000),
        Bar(datetime.now(), 100, 100, 100, 105, 1000),
        Bar(datetime.now(), 100, 100, 100, 95, 1000),
        Bar(datetime.now(), 100, 100, 100, 105, 1000),
    ]
    engine = BacktestEngine(10000)
    result = engine.run(bars, SimpleStrategy("test"))
    assert result['total_return'] > -0.01  # should not lose money on oscillation
