"""Portfolio rebalancing recommendation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


class PortfolioRebalanceError(ValueError):
    """Raised when a portfolio rebalance plan cannot be calculated."""


@dataclass(frozen=True)
class RebalanceTrade:
    """Single symbol rebalance recommendation."""

    symbol: str
    current_value: float
    target_value: float
    trade_value: float
    trade_quantity: float
    current_weight: float
    target_weight: float
    action: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the trade."""

        return {
            "symbol": self.symbol,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "trade_value": self.trade_value,
            "trade_quantity": self.trade_quantity,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "action": self.action,
        }


@dataclass(frozen=True)
class PortfolioRebalancePlan:
    """Collection of trades needed to move a portfolio toward target weights."""

    total_value: float
    tolerance: float
    trades: tuple[RebalanceTrade, ...]

    @property
    def trade_count(self) -> int:
        """Return the number of recommended trades."""

        return len(self.trades)

    @property
    def total_turnover(self) -> float:
        """Return absolute trade value as a share of total portfolio value."""

        if self.total_value == 0:
            return 0.0
        return sum(abs(trade.trade_value) for trade in self.trades) / self.total_value

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the rebalance plan."""

        return {
            "total_value": self.total_value,
            "tolerance": self.tolerance,
            "trade_count": self.trade_count,
            "total_turnover": self.total_turnover,
            "trades": [trade.to_dict() for trade in self.trades],
        }


def plan_portfolio_rebalance(
    positions: pd.DataFrame,
    target_weights: dict[str, float],
    *,
    symbol_column: str = "symbol",
    quantity_column: str = "quantity",
    price_column: str = "price",
    tolerance: float = 0.01,
    min_trade_value: float = 0.0,
) -> PortfolioRebalancePlan:
    """Create buy/sell recommendations to move positions toward target weights.

    This helper is designed for long-only research portfolios. Quantities and
    prices must be non-negative. Target weights are normalized automatically so
    callers can provide weights that sum to 1.0 or percentages that sum to 100.
    """

    if not isinstance(positions, pd.DataFrame):
        raise PortfolioRebalanceError("positions must be a pandas DataFrame")

    if not target_weights:
        raise PortfolioRebalanceError("target_weights cannot be empty")

    if tolerance < 0:
        raise PortfolioRebalanceError("tolerance cannot be negative")

    if min_trade_value < 0:
        raise PortfolioRebalanceError("min_trade_value cannot be negative")

    required = [symbol_column, quantity_column, price_column]
    missing = [column for column in required if column not in positions.columns]
    if missing:
        raise PortfolioRebalanceError(
            f"missing required position columns: {', '.join(missing)}"
        )

    clean = positions.copy()
    clean[quantity_column] = pd.to_numeric(clean[quantity_column], errors="coerce")
    clean[price_column] = pd.to_numeric(clean[price_column], errors="coerce")

    if clean[[quantity_column, price_column]].isna().any().any():
        raise PortfolioRebalanceError("quantity and price columns must be numeric")

    if (clean[quantity_column] < 0).any():
        raise PortfolioRebalanceError("rebalance planner only supports long positions")

    if (clean[price_column] < 0).any():
        raise PortfolioRebalanceError("price column cannot contain negative values")

    normalized_targets = _normalize_target_weights(target_weights)

    clean["_market_value"] = clean[quantity_column] * clean[price_column]
    grouped = clean.groupby(symbol_column, dropna=False).agg(
        market_value=("_market_value", "sum"),
        price=(price_column, "last"),
    )

    for symbol in normalized_targets:
        if symbol not in grouped.index:
            grouped.loc[symbol, ["market_value", "price"]] = [0.0, 0.0]

    total_value = float(grouped["market_value"].sum())
    if total_value <= 0:
        raise PortfolioRebalanceError("portfolio total value must be positive")

    trades: list[RebalanceTrade] = []

    for symbol, target_weight in sorted(normalized_targets.items()):
        current_value = float(grouped.loc[symbol, "market_value"])
        price = float(grouped.loc[symbol, "price"])
        target_value = total_value * target_weight
        trade_value = target_value - current_value
        current_weight = current_value / total_value

        if abs(target_weight - current_weight) <= tolerance:
            continue

        if abs(trade_value) < min_trade_value:
            continue

        if price == 0 and trade_value > 0:
            raise PortfolioRebalanceError(
                f"cannot buy {symbol} because no current price is available"
            )

        trade_quantity = 0.0 if price == 0 else trade_value / price
        action = "buy" if trade_value > 0 else "sell"

        trades.append(
            RebalanceTrade(
                symbol=str(symbol),
                current_value=current_value,
                target_value=float(target_value),
                trade_value=float(trade_value),
                trade_quantity=float(trade_quantity),
                current_weight=float(current_weight),
                target_weight=float(target_weight),
                action=action,
            )
        )

    return PortfolioRebalancePlan(
        total_value=total_value,
        tolerance=tolerance,
        trades=tuple(trades),
    )


def _normalize_target_weights(target_weights: dict[str, float]) -> dict[str, float]:
    numeric_targets: dict[str, float] = {}

    for symbol, weight in target_weights.items():
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError) as exc:
            raise PortfolioRebalanceError("target weights must be numeric") from exc

        if numeric_weight < 0:
            raise PortfolioRebalanceError("target weights cannot be negative")

        numeric_targets[str(symbol)] = numeric_weight

    total_weight = sum(numeric_targets.values())
    if total_weight <= 0:
        raise PortfolioRebalanceError("target weights must sum to a positive value")

    return {
        symbol: weight / total_weight
        for symbol, weight in numeric_targets.items()
    }
