"""Annualization helpers.

Turning a Sharpe/Sortino ratio computed on a per-PERIOD return series into
an annualized figure requires knowing how many periods actually occur in a
year. A single hardcoded constant (252, the number of trading DAYS in a US
equities year) is wrong for every other sampling frequency this system
actually uses - hourly OHLCV bars in a 24/7 crypto market, or a live/paper
equity curve sampled once per poll cycle - and using it anyway doesn't
crash anything (the same wrong constant is applied consistently to every
candidate and the baseline, so relative promotion comparisons still hold),
it just silently mislabels the result: the number stops meaning what
"annualized Sharpe" conventionally means, and a fixed promotion threshold
(e.g. min_sharpe_to_promote) ends up calibrated against a different scale
than anyone reading the number assumes.
"""

from __future__ import annotations

from typing import Optional

# Crypto markets trade 24/7, unlike TradFi's ~252-day year - every helper
# here assumes continuous, unbroken trading.
SECONDS_PER_YEAR = 365 * 24 * 3600

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def bars_per_year(timeframe: str) -> float:
    """Periods-per-year for a ccxt-style OHLCV timeframe string (e.g. "1h",
    "5m", "1d") in a 24/7 market. Falls back to 252 (the old hardcoded
    daily-trading-year assumption) for an unrecognized format, rather than
    raising - an annualization constant should never be the reason a
    walk-forward validation run crashes."""
    normalized = (timeframe or "").strip().lower()
    if len(normalized) < 2 or not normalized[:-1].isdigit() or normalized[-1] not in _UNIT_SECONDS:
        return 252.0
    bar_seconds = int(normalized[:-1]) * _UNIT_SECONDS[normalized[-1]]
    if bar_seconds <= 0:
        return 252.0
    return SECONDS_PER_YEAR / bar_seconds


def periods_per_year_from_seconds(interval_seconds: Optional[float]) -> float:
    """Periods-per-year for an equity curve sampled once every
    `interval_seconds` (e.g. Orchestrator's poll_interval_seconds) - same
    reasoning as bars_per_year, for a fixed wall-clock sampling cadence
    instead of an OHLCV bar size."""
    if interval_seconds is None or interval_seconds <= 0:
        return 252.0
    return SECONDS_PER_YEAR / interval_seconds
