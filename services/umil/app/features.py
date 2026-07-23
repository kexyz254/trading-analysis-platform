"""Closed-candle-only feature calculations for Market DNA."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

from .observations import Candle, only_closed


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    result = statistics.fmean(values[:period])
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [current - previous for previous, current in zip(values, values[1:])]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    average_gain = statistics.fmean(gains)
    average_loss = statistics.fmean(losses)
    if average_loss <= 1e-15:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _atr(candles: tuple[Candle, ...], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    ranges = []
    for previous, current in zip(candles, candles[1:]):
        ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return statistics.fmean(ranges[-period:])


def _realized_volatility(values: list[float], period: int = 30) -> float | None:
    if len(values) <= period:
        return None
    returns = [math.log(current / previous) for previous, current in zip(values, values[1:])]
    return statistics.pstdev(returns[-period:]) * math.sqrt(period) * 100.0


def _zscore(value: float, history: list[float]) -> float | None:
    if len(history) < 20:
        return None
    sample = history[-60:]
    deviation = statistics.pstdev(sample)
    if deviation <= 1e-15:
        return 0.0
    return (value - statistics.fmean(sample)) / deviation


@dataclass(frozen=True, slots=True)
class TimeframeFeatures:
    interval: str
    as_of: str
    sample_count: int
    return_1_pct: float
    return_6_pct: float | None
    return_24_pct: float | None
    ema_fast: float | None
    ema_slow: float | None
    ema_slope_pct: float | None
    rsi_14: float | None
    momentum_acceleration: float | None
    atr_pct: float | None
    realized_volatility_pct: float | None
    relative_volume_z: float | None
    taker_buy_ratio: float | None
    range_position: float | None
    structure_state: str
    completeness: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def calculate_timeframe_features(candles: Iterable[Candle]) -> TimeframeFeatures:
    rows = only_closed(candles)
    if not rows:
        raise ValueError("at least one closed candle is required")
    interval = rows[0].interval
    instrument_id = rows[0].instrument_id
    if any(row.interval != interval or row.instrument_id != instrument_id for row in rows):
        raise ValueError("feature batch must contain one instrument and interval")
    closes = [row.close for row in rows]
    volumes = [row.quote_volume for row in rows]
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    previous_fast = _ema(closes[:-3], 12) if len(closes) >= 15 else None
    slope = None
    if fast is not None and previous_fast is not None and previous_fast > 0:
        slope = (fast / previous_fast - 1.0) * 100.0
    atr = _atr(rows)
    rsi = _rsi(closes)
    earlier_rsi = _rsi(closes[:-3]) if len(closes) > 17 else None
    lookback = rows[-20:]
    range_high = max(row.high for row in lookback)
    range_low = min(row.low for row in lookback)
    range_position = (
        (rows[-1].close - range_low) / (range_high - range_low)
        if range_high > range_low else 0.5
    )
    previous_high = max((row.high for row in rows[-21:-1]), default=rows[-1].high)
    previous_low = min((row.low for row in rows[-21:-1]), default=rows[-1].low)
    if rows[-1].close > previous_high:
        structure = "BREAKOUT_UP"
    elif rows[-1].close < previous_low:
        structure = "BREAKOUT_DOWN"
    elif fast is not None and slow is not None and fast > slow and range_position >= 0.55:
        structure = "TRENDING_UP"
    elif fast is not None and slow is not None and fast < slow and range_position <= 0.45:
        structure = "TRENDING_DOWN"
    else:
        structure = "RANGE"
    completeness = min(1.0, len(rows) / 60.0)
    quote_volume = rows[-1].quote_volume
    taker_ratio = (
        rows[-1].taker_buy_quote_volume / quote_volume if quote_volume > 0 else None
    )
    return TimeframeFeatures(
        interval=interval,
        as_of=rows[-1].close_time.isoformat(),
        sample_count=len(rows),
        return_1_pct=(closes[-1] / closes[-2] - 1.0) * 100.0 if len(closes) > 1 else 0.0,
        return_6_pct=(closes[-1] / closes[-7] - 1.0) * 100.0 if len(closes) > 6 else None,
        return_24_pct=(closes[-1] / closes[-25] - 1.0) * 100.0 if len(closes) > 24 else None,
        ema_fast=fast,
        ema_slow=slow,
        ema_slope_pct=slope,
        rsi_14=rsi,
        momentum_acceleration=(rsi - earlier_rsi) if rsi is not None and earlier_rsi is not None else None,
        atr_pct=(atr / closes[-1] * 100.0) if atr is not None else None,
        realized_volatility_pct=_realized_volatility(closes),
        relative_volume_z=_zscore(volumes[-1], volumes[:-1]),
        taker_buy_ratio=taker_ratio,
        range_position=range_position,
        structure_state=structure,
        completeness=completeness,
    )


def calculate_multi_timeframe_features(
    candles_by_interval: Mapping[str, Iterable[Candle]],
) -> dict[str, TimeframeFeatures]:
    output: dict[str, TimeframeFeatures] = {}
    for interval, candles in candles_by_interval.items():
        rows = tuple(candles)
        if rows:
            output[interval] = calculate_timeframe_features(rows)
    return output
