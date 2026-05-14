"""Backtesting engine for trading strategies."""
from typing import List
from trading_assistant.core.ohlc import Bar
from trading_assistant.strategies.base import BaseStrategy, Signal

class BacktestEngine:
    """Run backtests on historical data."""
    
    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital

    def run(self, bars: List[Bar], strategy: BaseStrategy) -> dict:
        """Run backtest and return results."""
        capital = self.initial_capital
        position = 0
        trades = 0

        for bar in bars:
            # Skip if price is zero or negative
            if bar.close <= 0:
                print(f"Warning: skipping trade at price {bar.close}")
                continue
                
            signal = strategy.generate_signal({"price": bar.close})
            if signal == Signal.BUY and position == 0:
                position = capital / bar.close
                capital = 0
                trades += 1
            elif signal == Signal.SELL and position > 0:
                capital = position * bar.close
                position = 0
                trades += 1

        final_value = capital + (position * bars[-1].close if position > 0 else 0)
        total_return = (final_value - self.initial_capital) / self.initial_capital
        return {"total_return": total_return, "total_trades": trades}

import logging
logging.basicConfig(level=logging.INFO)

    # Add to run method after buy/sell
    # logging.info(f"BUY at {bar.close}")
    # logging.info(f"SELL at {bar.close}")
