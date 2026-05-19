import pandas as pd
import numpy as np
from typing import Dict, List, Any
from dataclasses import dataclass

@dataclass
class TradeRecord:
    timestamp: Any
    price: float
    signal: str
    position: int = 0
    pnl: float = 0.0
    cumulative_return: float = 0.0

class TradeSimulator:
    def __init__(self, initial_capital: float = 10000.0, commission: float = 0.001):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.commission = commission
        self.trades = []
        self.position = 0
        self.equity_curve = [initial_capital]

    def simulate(self, strategy, data):
        self.reset()
        for idx in range(len(data)):
            price_window = data[:idx+1]
            current_price = price_window.iloc[-1]
            signal = strategy.generate_signal(price_window)
            if signal == "BUY" and self.position <= 0:
                if current_price > 0:
                    shares = self.capital / current_price
                    cost = shares * current_price
                    fee = cost * self.commission
                    self.position = shares
                    self.capital -= (cost + fee)
            elif signal == "SELL" and self.position > 0:
                if current_price > 0:
                    proceeds = self.position * current_price
                    fee = proceeds * self.commission
                    self.capital += (proceeds - fee)
                    self.position = 0
            position_value = self.position * current_price
            total_value = self.capital + position_value
            self.equity_curve.append(total_value)
            self.trades.append(TradeRecord(
                timestamp=data.index[idx] if hasattr(data, "index") else idx,
                price=current_price,
                signal=signal,
                position=self.position,
                pnl=self.capital - self.initial_capital,
                cumulative_return=(self.capital / self.initial_capital - 1) * 100 if self.initial_capital > 0 else 0
            ))
        return self.trades

    def reset(self):
        self.capital = self.initial_capital
        self.position = 0
        self.trades = []
        self.equity_curve = [self.initial_capital]

    def summary(self):
        if not self.trades:
            return {"error": "no trades executed"}
        final_value = self.equity_curve[-1] if self.equity_curve else self.capital
        total_return = (final_value - self.initial_capital) / self.initial_capital * 100 if self.initial_capital > 0 else 0
        buys = sum(1 for t in self.trades if t.signal == "BUY")
        sells = sum(1 for t in self.trades if t.signal == "SELL")
        holds = sum(1 for t in self.trades if t.signal == "HOLD")
        returns = []
        for i in range(1, len(self.equity_curve)):
            if self.equity_curve[i-1] > 0:
                ret = (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1]
                returns.append(ret)
        returns_series = pd.Series(returns) if returns else pd.Series([0])
        sharpe = 0
        if returns_series.std() != 0:
            sharpe = returns_series.mean() / returns_series.std() * np.sqrt(252)
        return {
            "total_trades": len(self.trades),
            "buy_signals": buys,
            "sell_signals": sells,
            "hold_signals": holds,
            "final_capital": final_value,
            "total_return_pct": total_return,
            "sharpe_ratio": sharpe,
            "max_drawdown": self._max_drawdown()
        }

    def _max_drawdown(self):
        if len(self.equity_curve) < 2:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            if peak > 0:
                dd = (peak - value) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd * 100
