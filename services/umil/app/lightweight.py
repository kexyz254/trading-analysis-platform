"""All-symbol intelligence derived from the one-second universe projection."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from .contracts import MarketTicker
from .normalization import percentile_rank


def calculate_lightweight_states(
    tickers: Iterable[MarketTicker], *, as_of: datetime, stale_after_seconds: float,
) -> dict[str, dict[str, object]]:
    rows = tuple(tickers)
    volume_population = [row.quote_volume_24h for row in rows]
    return_population = [row.return_24h_pct for row in rows]
    output: dict[str, dict[str, object]] = {}
    for row in rows:
        age = max(0.0, (as_of - row.event_time).total_seconds())
        range_width = row.high_price_24h - row.low_price_24h
        position = (
            (row.last_price - row.low_price_24h) / range_width
            if range_width > 0 else None
        )
        range_pct = range_width / row.last_price * 100.0
        return_percentile = percentile_rank(row.return_24h_pct, return_population)
        volume_percentile = percentile_rank(row.quote_volume_24h, volume_population)
        flags: list[str] = []
        if age > stale_after_seconds:
            flags.append("STALE_TICKER")
        if row.spread_bps is None:
            flags.append("SPREAD_UNAVAILABLE")
        if position is None:
            flags.append("RANGE_UNAVAILABLE")
        if row.return_24h_pct >= 2:
            trend = "ADVANCING"
        elif row.return_24h_pct <= -2:
            trend = "DECLINING"
        else:
            trend = "MIXED"
        anomaly_score = max(
            abs(return_percentile - 50.0) * 2.0,
            abs(volume_percentile - 50.0) * 2.0 if volume_percentile >= 90 else 0.0,
            min(100.0, range_pct * 5.0),
        )
        output[row.instrument_id] = {
            "state": "STALE" if "STALE_TICKER" in flags else "READY",
            "as_of": row.event_time,
            "trend": trend,
            "return_24h_pct": row.return_24h_pct,
            "return_percentile": round(return_percentile, 2),
            "range_position": round(position, 4) if position is not None else None,
            "intraday_range_pct": round(range_pct, 4),
            "volume_percentile": round(volume_percentile, 2),
            "spread_bps": row.spread_bps,
            "anomaly_score": round(anomaly_score, 2),
            "quality_flags": flags,
            "available_dimensions": [
                "trend", "structure", "volatility", "activity",
                *( ["liquidity"] if row.spread_bps is not None else [] ),
            ],
            "missing_dimensions": ["momentum", "pressure", "derivatives"],
            "directional_probability": None,
        }
    return output
