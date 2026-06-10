"""Portfolio target allocation drift reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .rebalance import PortfolioRebalanceError


@dataclass(frozen=True)
class AllocationDrift:
    """Current-vs-target allocation drift for one symbol."""

    symbol: str
    current_value: float
    target_value: float
    current_weight: float
    target_weight: float
    drift: float

    @property
    def absolute_drift(self) -> float:
        """Return the absolute allocation drift."""

        return abs(self.drift)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of allocation drift."""

        return {
            "symbol": self.symbol,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "drift": self.drift,
            "absolute_drift": self.absolute_drift,
        }


@dataclass(frozen=True)
class AllocationDriftReport:
    """Portfolio-wide allocation drift report."""

    total_value: float
    tolerance: float
    drifts: tuple[AllocationDrift, ...]

    @property
    def out_of_tolerance_symbols(self) -> tuple[str, ...]:
        """Return symbols whose absolute drift exceeds tolerance."""

        return tuple(
            drift.symbol
            for drift in self.drifts
            if drift.absolute_drift > self.tolerance
        )

    @property
    def max_absolute_drift(self) -> float:
        """Return the largest absolute drift in the report."""

        if not self.drifts:
            return 0.0
        return max(drift.absolute_drift for drift in self.drifts)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the drift report."""

        return {
            "total_value": self.total_value,
            "tolerance": self.tolerance,
            "out_of_tolerance_symbols": list(self.out_of_tolerance_symbols),
            "max_absolute_drift": self.max_absolute_drift,
            "drifts": [drift.to_dict() for drift in self.drifts],
        }


def analyze_allocation_drift(
    positions: pd.DataFrame,
    target_weights: dict[str, float],
    *,
    symbol_column: str = "symbol",
    quantity_column: str = "quantity",
    price_column: str = "price",
    tolerance: float = 0.01,
) -> AllocationDriftReport:
    """Compare current long-only portfolio weights with target weights."""

    if not isinstance(positions, pd.DataFrame):
        raise PortfolioRebalanceError("positions must be a pandas DataFrame")

    if not target_weights:
        raise PortfolioRebalanceError("target_weights cannot be empty")

    if tolerance < 0:
        raise PortfolioRebalanceError("tolerance cannot be negative")

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
        raise PortfolioRebalanceError("allocation drift only supports long positions")

    if (clean[price_column] < 0).any():
        raise PortfolioRebalanceError("price column cannot contain negative values")

    normalized_targets = _normalize_target_weights(target_weights)

    clean["_market_value"] = clean[quantity_column] * clean[price_column]
    current_values = clean.groupby(symbol_column, dropna=False)["_market_value"].sum()

    total_value = float(current_values.sum())
    if total_value <= 0:
        raise PortfolioRebalanceError("portfolio total value must be positive")

    symbols = sorted(set(map(str, current_values.index)) | set(normalized_targets))
    drifts: list[AllocationDrift] = []

    for symbol in symbols:
        current_value = float(current_values.get(symbol, 0.0))
        current_weight = current_value / total_value
        target_weight = normalized_targets.get(symbol, 0.0)
        target_value = total_value * target_weight

        drifts.append(
            AllocationDrift(
                symbol=symbol,
                current_value=current_value,
                target_value=float(target_value),
                current_weight=float(current_weight),
                target_weight=float(target_weight),
                drift=float(current_weight - target_weight),
            )
        )

    return AllocationDriftReport(
        total_value=total_value,
        tolerance=tolerance,
        drifts=tuple(drifts),
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
