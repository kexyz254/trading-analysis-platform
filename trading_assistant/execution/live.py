"""Lightweight live trading engine for paper and exchange-backed execution."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from trading_assistant.portfolio.manager import PortfolioManager, RiskManager
from trading_assistant.strategies.base import BaseStrategy, Signal


class LiveTradingEngine:
    """Run one trading decision at a time against a live feed frame."""

    def __init__(
        self,
        strategy: BaseStrategy,
        portfolio: Optional[PortfolioManager] = None,
        risk_manager: Optional[RiskManager] = None,
        exchange=None,
        symbol: str = "BTC/USDT",
        risk_per_trade: float = 0.02,
        stop_loss_pct: float = 0.01,
    ):
        self.strategy = strategy
        self.portfolio = portfolio or PortfolioManager()
        self.risk_manager = risk_manager or RiskManager()
        self.exchange = exchange
        self.symbol = symbol
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct

    def run_once(self, frame: pd.DataFrame) -> Optional[dict]:
        """Evaluate the latest bar and place an order if the strategy returns a buy/sell signal."""
        if frame.empty:
            return None

        latest_price = float(frame["close"].iloc[-1])
        signal = self.strategy.generate_signal(frame)

        if signal == Signal.BUY and self._can_trade(latest_price):
            quantity = self._calculate_quantity(latest_price)
            if self.exchange is not None:
                self.exchange.create_market_buy_order(self.symbol, quantity)
            self.portfolio.open_position(self.symbol, latest_price, quantity)
            return {"signal": signal, "price": latest_price, "quantity": quantity}

        if signal == Signal.SELL and self.symbol in self.portfolio.positions:
            self.portfolio.close_position(self.symbol, latest_price)
            return {"signal": signal, "price": latest_price, "quantity": self.portfolio.positions.get(self.symbol, {}).get("quantity", 0)}

        return {"signal": signal, "price": latest_price, "quantity": 0}

    def _can_trade(self, price: float) -> bool:
        if price <= 0:
            return False
        if self.symbol in self.portfolio.positions:
            return False
        return True

    def _calculate_quantity(self, price: float) -> float:
        raw_quantity = self.risk_manager.calculate_position_size(
            self.portfolio.capital,
            risk_per_trade=self.risk_per_trade,
            stop_loss_pct=self.stop_loss_pct,
        )
        quantity = raw_quantity / price
        return round(quantity, 6)
