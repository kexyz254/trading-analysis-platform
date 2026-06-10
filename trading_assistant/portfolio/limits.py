"""Portfolio risk limit checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .exposure import (
    PortfolioExposureError,
    summarize_portfolio_exposure,
    summarize_position_concentration,
)


@dataclass(frozen=True)
class PortfolioLimitViolation:
    """A single portfolio limit breach."""

    limit_name: str
    actual_value: float
    limit_value: float
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the violation."""

        return {
            "limit_name": self.limit_name,
            "actual_value": self.actual_value,
            "limit_value": self.limit_value,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class PortfolioLimitReport:
    """Summary of portfolio risk-limit checks."""

    checked_limits: tuple[str, ...]
    violations: tuple[PortfolioLimitViolation, ...]

    @property
    def is_within_limits(self) -> bool:
        """Return True when no limits were breached."""

        return len(self.violations) == 0

    @property
    def violation_count(self) -> int:
        """Return the number of limit violations."""

        return len(self.violations)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the report."""

        return {
            "checked_limits": list(self.checked_limits),
            "is_within_limits": self.is_within_limits,
            "violation_count": self.violation_count,
            "violations": [violation.to_dict() for violation in self.violations],
        }


def check_portfolio_limits(
    positions: pd.DataFrame,
    *,
    max_position_weight: float | None = None,
    max_gross_exposure: float | None = None,
    max_net_exposure: float | None = None,
    max_short_exposure: float | None = None,
    symbol_column: str = "symbol",
    quantity_column: str = "quantity",
    price_column: str = "price",
) -> PortfolioLimitReport:
    """Check portfolio positions against configurable risk limits."""

    checked_limits: list[str] = []
    violations: list[PortfolioLimitViolation] = []

    exposure = summarize_portfolio_exposure(
        positions,
        symbol_column=symbol_column,
        quantity_column=quantity_column,
        price_column=price_column,
    )

    if max_position_weight is not None:
        _validate_ratio_limit(max_position_weight, "max_position_weight")
        checked_limits.append("max_position_weight")

        concentration = summarize_position_concentration(
            positions,
            symbol_column=symbol_column,
            quantity_column=quantity_column,
            price_column=price_column,
            max_weight=max_position_weight,
        )

        for symbol in concentration.over_limit_symbols:
            violations.append(
                PortfolioLimitViolation(
                    limit_name="max_position_weight",
                    actual_value=concentration.position_weights[symbol],
                    limit_value=max_position_weight,
                    symbol=symbol,
                )
            )

    if max_gross_exposure is not None:
        _validate_non_negative_limit(max_gross_exposure, "max_gross_exposure")
        checked_limits.append("max_gross_exposure")
        if exposure.gross_exposure > max_gross_exposure:
            violations.append(
                PortfolioLimitViolation(
                    limit_name="max_gross_exposure",
                    actual_value=exposure.gross_exposure,
                    limit_value=max_gross_exposure,
                )
            )

    if max_net_exposure is not None:
        _validate_non_negative_limit(max_net_exposure, "max_net_exposure")
        checked_limits.append("max_net_exposure")
        actual_net = abs(exposure.net_exposure)
        if actual_net > max_net_exposure:
            violations.append(
                PortfolioLimitViolation(
                    limit_name="max_net_exposure",
                    actual_value=actual_net,
                    limit_value=max_net_exposure,
                )
            )

    if max_short_exposure is not None:
        _validate_non_negative_limit(max_short_exposure, "max_short_exposure")
        checked_limits.append("max_short_exposure")
        if exposure.short_exposure > max_short_exposure:
            violations.append(
                PortfolioLimitViolation(
                    limit_name="max_short_exposure",
                    actual_value=exposure.short_exposure,
                    limit_value=max_short_exposure,
                )
            )

    if not checked_limits:
        raise PortfolioExposureError("at least one portfolio limit must be provided")

    return PortfolioLimitReport(
        checked_limits=tuple(checked_limits),
        violations=tuple(violations),
    )


def _validate_ratio_limit(value: float, name: str) -> None:
    if not 0 <= value <= 1:
        raise PortfolioExposureError(f"{name} must be between 0 and 1")


def _validate_non_negative_limit(value: float, name: str) -> None:
    if value < 0:
        raise PortfolioExposureError(f"{name} cannot be negative")
