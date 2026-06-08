"""Portfolio exposure reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


class PortfolioExposureError(ValueError):
    """Raised when portfolio position data cannot be summarized."""


@dataclass(frozen=True)
class PortfolioExposureSummary:
    """Long, short, gross, and net exposure summary for a portfolio."""

    position_count: int
    long_exposure: float
    short_exposure: float
    gross_exposure: float
    net_exposure: float
    largest_symbol: str | None
    largest_position_value: float

    @property
    def long_ratio(self) -> float:
        """Return long exposure as a share of gross exposure."""

        if self.gross_exposure == 0:
            return 0.0
        return self.long_exposure / self.gross_exposure

    @property
    def short_ratio(self) -> float:
        """Return short exposure as a share of gross exposure."""

        if self.gross_exposure == 0:
            return 0.0
        return self.short_exposure / self.gross_exposure

    @property
    def net_ratio(self) -> float:
        """Return net exposure as a share of gross exposure."""

        if self.gross_exposure == 0:
            return 0.0
        return self.net_exposure / self.gross_exposure

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the exposure summary."""

        return {
            "position_count": self.position_count,
            "long_exposure": self.long_exposure,
            "short_exposure": self.short_exposure,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "long_ratio": self.long_ratio,
            "short_ratio": self.short_ratio,
            "net_ratio": self.net_ratio,
            "largest_symbol": self.largest_symbol,
            "largest_position_value": self.largest_position_value,
        }


def summarize_portfolio_exposure(
    positions: pd.DataFrame,
    *,
    symbol_column: str = "symbol",
    quantity_column: str = "quantity",
    price_column: str = "price",
) -> PortfolioExposureSummary:
    """Summarize portfolio exposure from position quantities and prices.

    Positive quantities are treated as long positions. Negative quantities are
    treated as short positions. Position value is calculated as quantity * price.
    """

    if not isinstance(positions, pd.DataFrame):
        raise PortfolioExposureError("positions must be a pandas DataFrame")

    required = [symbol_column, quantity_column, price_column]
    missing = [column for column in required if column not in positions.columns]
    if missing:
        raise PortfolioExposureError(
            f"missing required position columns: {', '.join(missing)}"
        )

    if positions.empty:
        return PortfolioExposureSummary(
            position_count=0,
            long_exposure=0.0,
            short_exposure=0.0,
            gross_exposure=0.0,
            net_exposure=0.0,
            largest_symbol=None,
            largest_position_value=0.0,
        )

    clean = positions.copy()
    clean[quantity_column] = pd.to_numeric(clean[quantity_column], errors="coerce")
    clean[price_column] = pd.to_numeric(clean[price_column], errors="coerce")

    if clean[[quantity_column, price_column]].isna().any().any():
        raise PortfolioExposureError("quantity and price columns must be numeric")

    if (clean[price_column] < 0).any():
        raise PortfolioExposureError("price column cannot contain negative values")

    clean["_market_value"] = clean[quantity_column] * clean[price_column]
    clean["_absolute_value"] = clean["_market_value"].abs()

    long_exposure = float(clean.loc[clean["_market_value"] > 0, "_market_value"].sum())
    short_exposure = float(
        clean.loc[clean["_market_value"] < 0, "_market_value"].abs().sum()
    )
    gross_exposure = long_exposure + short_exposure
    net_exposure = long_exposure - short_exposure

    largest_index = clean["_absolute_value"].idxmax()
    largest_symbol = str(clean.loc[largest_index, symbol_column])
    largest_position_value = float(clean.loc[largest_index, "_absolute_value"])

    return PortfolioExposureSummary(
        position_count=len(clean),
        long_exposure=long_exposure,
        short_exposure=short_exposure,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        largest_symbol=largest_symbol,
        largest_position_value=largest_position_value,
    )



@dataclass(frozen=True)
class PortfolioConcentrationSummary:
    """Position concentration summary based on absolute market value."""

    total_market_value: float
    top_symbol: str | None
    top_weight: float
    position_weights: dict[str, float]
    over_limit_symbols: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of concentration risk."""

        return {
            "total_market_value": self.total_market_value,
            "top_symbol": self.top_symbol,
            "top_weight": self.top_weight,
            "position_weights": dict(self.position_weights),
            "over_limit_symbols": list(self.over_limit_symbols),
        }


def summarize_position_concentration(
    positions: pd.DataFrame,
    *,
    symbol_column: str = "symbol",
    quantity_column: str = "quantity",
    price_column: str = "price",
    max_weight: float = 0.25,
) -> PortfolioConcentrationSummary:
    """Summarize portfolio concentration by absolute position value."""

    if not 0 <= max_weight <= 1:
        raise PortfolioExposureError("max_weight must be between 0 and 1")

    if not isinstance(positions, pd.DataFrame):
        raise PortfolioExposureError("positions must be a pandas DataFrame")

    required = [symbol_column, quantity_column, price_column]
    missing = [column for column in required if column not in positions.columns]
    if missing:
        raise PortfolioExposureError(
            f"missing required position columns: {', '.join(missing)}"
        )

    if positions.empty:
        return PortfolioConcentrationSummary(
            total_market_value=0.0,
            top_symbol=None,
            top_weight=0.0,
            position_weights={},
            over_limit_symbols=(),
        )

    clean = positions.copy()
    clean[quantity_column] = pd.to_numeric(clean[quantity_column], errors="coerce")
    clean[price_column] = pd.to_numeric(clean[price_column], errors="coerce")

    if clean[[quantity_column, price_column]].isna().any().any():
        raise PortfolioExposureError("quantity and price columns must be numeric")

    if (clean[price_column] < 0).any():
        raise PortfolioExposureError("price column cannot contain negative values")

    clean["_absolute_value"] = (clean[quantity_column] * clean[price_column]).abs()
    grouped = clean.groupby(symbol_column, dropna=False)["_absolute_value"].sum()

    total_market_value = float(grouped.sum())
    if total_market_value == 0:
        return PortfolioConcentrationSummary(
            total_market_value=0.0,
            top_symbol=None,
            top_weight=0.0,
            position_weights={str(symbol): 0.0 for symbol in grouped.index},
            over_limit_symbols=(),
        )

    weights = {
        str(symbol): float(value / total_market_value)
        for symbol, value in grouped.items()
    }
    top_symbol = max(weights, key=weights.get)
    top_weight = weights[top_symbol]
    over_limit_symbols = tuple(
        symbol for symbol, weight in weights.items() if weight > max_weight
    )

    return PortfolioConcentrationSummary(
        total_market_value=total_market_value,
        top_symbol=top_symbol,
        top_weight=top_weight,
        position_weights=weights,
        over_limit_symbols=over_limit_symbols,
    )
