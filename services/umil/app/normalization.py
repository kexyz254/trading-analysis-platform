"""Robust point-in-time normalization without look-ahead leakage."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable


def _clean(values: Iterable[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in values if math.isfinite(float(value)))


def winsorize(values: Iterable[float], *, tail: float = 0.025) -> tuple[float, ...]:
    rows = sorted(_clean(values))
    if not rows:
        return ()
    if not 0 <= tail < 0.5:
        raise ValueError("tail must be in [0, 0.5)")
    lower = rows[min(len(rows) - 1, int((len(rows) - 1) * tail))]
    upper = rows[max(0, int((len(rows) - 1) * (1.0 - tail)))]
    return tuple(max(lower, min(upper, value)) for value in rows)


def percentile_rank(value: float, population: Iterable[float]) -> float:
    rows = _clean(population)
    if not rows:
        return 50.0
    less = sum(item < value for item in rows)
    equal = sum(item == value for item in rows)
    return 100.0 * (less + 0.5 * equal) / len(rows)


def robust_zscore(value: float, history: Iterable[float], *, cap: float = 6.0) -> float:
    rows = winsorize(history)
    if len(rows) < 5:
        return 0.0
    median = statistics.median(rows)
    mad = statistics.median(abs(item - median) for item in rows)
    if mad <= 1e-12:
        return 0.0
    score = 0.67448975 * (float(value) - median) / mad
    return max(-cap, min(cap, score))


@dataclass(frozen=True, slots=True)
class NormalizedSignal:
    score: float
    time_series_percentile: float
    cross_sectional_percentile: float
    history_count: int
    peer_count: int


def blended_percentile(
    value: float,
    *,
    history: Iterable[float],
    peers: Iterable[float],
    higher_is_stronger: bool = True,
) -> NormalizedSignal:
    historical = _clean(history)
    cross_section = _clean(peers)
    time_score = percentile_rank(value, historical)
    peer_score = percentile_rank(value, cross_section)
    if not higher_is_stronger:
        time_score = 100.0 - time_score
        peer_score = 100.0 - peer_score
    history_weight = min(0.6, len(historical) / 200.0 * 0.6)
    peer_weight = min(0.4, len(cross_section) / 50.0 * 0.4)
    evidence_weight = history_weight + peer_weight
    score = 50.0 if evidence_weight == 0 else (
        time_score * history_weight + peer_score * peer_weight
    ) / evidence_weight
    return NormalizedSignal(
        score=round(max(0.0, min(100.0, score)), 4),
        time_series_percentile=round(time_score, 4),
        cross_sectional_percentile=round(peer_score, 4),
        history_count=len(historical),
        peer_count=len(cross_section),
    )
