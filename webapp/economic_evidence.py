"""Point-in-time eligibility and aggregate economic evidence.

The policy in this module deliberately separates three questions:

* Was a forecast issued close enough to the exchange cycle boundary to be a
  full-horizon observation?
* Did the forecast's confidence-adjusted movement estimate clear modeled
  round-trip friction with a safety margin?
* How was the official close delivered (live stream or REST recovery)?

Only the first question controls the primary statistical benchmark.  The
second controls simulated exposure.  The third is reported as provenance and
never changes the official Binance close price.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence


ECONOMIC_EVIDENCE_VERSION = "economic-evidence-v1.3.0"
BOUNDARY_ISSUE_TOLERANCE_MS = 10_000
ECONOMIC_COST_SAFETY_MULTIPLIER = Decimal("1.20")
MOVEMENT_MODEL_VERSION = "tick-variance-time-scaled-v1"
PRIMARY_SAMPLE_MINIMUM = 100


@dataclass(frozen=True)
class TimingAssessment:
    policy_version: str
    timing_class: str
    primary_eligible: bool
    exclusion_code: Optional[str]
    issue_delay_ms: int
    forecast_lead_ms: int
    forecast_lead_ratio: float


@dataclass(frozen=True)
class EconomicAssessment:
    action: str
    decision_code: str
    expected_move_bps: float
    confidence_adjusted_move_bps: float
    round_trip_cost_bps: float
    cost_hurdle_bps: float
    movement_model_version: str


def assess_issue_timing(
    *,
    horizon_ms: int,
    cycle_start_ms: int,
    due_at_ms: int,
    decision_at_ms: int,
) -> TimingAssessment:
    """Classify a decision without consulting any future market observation."""
    horizon_ms = int(horizon_ms)
    cycle_start_ms = int(cycle_start_ms)
    due_at_ms = int(due_at_ms)
    decision_at_ms = int(decision_at_ms)
    issue_delay_ms = decision_at_ms - cycle_start_ms
    lead_ms = due_at_ms - decision_at_ms
    ratio = lead_ms / horizon_ms if horizon_ms > 0 else 0.0

    invalid = (
        horizon_ms <= 0
        or due_at_ms - cycle_start_ms != horizon_ms
        or issue_delay_ms < 0
        or lead_ms <= 0
        or ratio > 1.0
    )
    if invalid:
        timing_class = "INVALID_TIMING"
        exclusion = "INVALID_TIMING"
        eligible = False
    elif issue_delay_ms <= BOUNDARY_ISSUE_TOLERANCE_MS:
        timing_class = "BOUNDARY_ISSUED"
        exclusion = None
        eligible = True
    else:
        timing_class = "LATE_ISSUED"
        exclusion = "LATE_ISSUANCE"
        eligible = False

    return TimingAssessment(
        policy_version=ECONOMIC_EVIDENCE_VERSION,
        timing_class=timing_class,
        primary_eligible=eligible,
        exclusion_code=exclusion,
        issue_delay_ms=issue_delay_ms,
        forecast_lead_ms=lead_ms,
        forecast_lead_ratio=max(0.0, min(1.0, ratio)),
    )


def _tick_price(tick: Mapping[str, Any]) -> float:
    value = float(tick["price"])
    if not math.isfinite(value) or value <= 0:
        raise ValueError("tick prices must be finite and positive")
    return value


def estimate_expected_move_bps(
    ticks: Sequence[Mapping[str, Any]],
    forecast_lead_ms: int,
    decision_at_ms: int,
) -> float:
    """Estimate absolute movement from only the ticks known at decision time.

    Squared log returns are scaled from the observed tick-window duration to
    the remaining forecast duration.  This is a conservative research proxy,
    not a promise of future volatility, and is capped against corrupt feeds.
    """
    observations = []
    for tick in ticks:
        timestamp = int(tick.get("trade_ms") or tick.get("event_ms") or 0)
        if timestamp <= 0 or timestamp > int(decision_at_ms):
            continue
        observations.append((timestamp, _tick_price(tick)))
    observations.sort(key=lambda item: item[0])
    if len(observations) < 3 or int(forecast_lead_ms) <= 0:
        return 0.0

    first_ms = observations[0][0]
    last_ms = observations[-1][0]
    span_ms = last_ms - first_ms
    if span_ms <= 0:
        return 0.0
    returns = [
        math.log(observations[index][1] / observations[index - 1][1])
        for index in range(1, len(observations))
        if observations[index][0] > observations[index - 1][0]
    ]
    if not returns:
        return 0.0
    scaled_variance = sum(value * value for value in returns) * (
        int(forecast_lead_ms) / span_ms
    )
    expected_absolute_log_move = math.sqrt(2.0 / math.pi) * math.sqrt(
        max(0.0, scaled_variance)
    )
    return min(5_000.0, expected_absolute_log_move * 10_000.0)


def assess_economic_action(
    *,
    direction: str,
    confidence: float,
    ticks: Sequence[Mapping[str, Any]],
    forecast_lead_ms: int,
    decision_at_ms: int,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    timing_eligible: bool,
) -> EconomicAssessment:
    direction = str(direction).upper()
    if direction not in {"UP", "DOWN"}:
        raise ValueError("economic direction must be UP or DOWN")
    confidence = max(0.0, min(1.0, float(confidence)))
    expected_move = estimate_expected_move_bps(
        ticks, forecast_lead_ms, decision_at_ms
    )
    adjusted_move = expected_move * confidence
    round_trip_cost = float(
        (Decimal(fee_rate) + Decimal(slippage_rate))
        * Decimal("2")
        * Decimal("10000")
    )
    hurdle = float(
        Decimal(str(round_trip_cost)) * ECONOMIC_COST_SAFETY_MULTIPLIER
    )
    if not timing_eligible:
        action = "NO_TRADE"
        code = "TIMING_INELIGIBLE"
    elif adjusted_move <= hurdle:
        action = "NO_TRADE"
        code = "COST_HURDLE"
    else:
        action = direction
        code = "ECONOMICALLY_ELIGIBLE"
    return EconomicAssessment(
        action=action,
        decision_code=code,
        expected_move_bps=expected_move,
        confidence_adjusted_move_bps=adjusted_move,
        round_trip_cost_bps=round_trip_cost,
        cost_hurdle_bps=hurdle,
        movement_model_version=MOVEMENT_MODEL_VERSION,
    )


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )


def _hypothetical_pnl(
    row: sqlite3.Row, fee_rate: Decimal, slippage_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    entry = Decimal(str(row["entry_price_text"] or row["price_to_beat"]))
    close = Decimal(str(row["actual_price_text"] or row["actual_price"]))
    stake = Decimal("1")
    quantity = stake / entry
    signed_move = close - entry
    if row["direction"] == "DOWN":
        signed_move = -signed_move
    gross = quantity * signed_move
    exit_notional = quantity * close
    costs = (stake + exit_notional) * (fee_rate + slippage_rate)
    return gross, costs, gross - costs


def _sample_metrics(
    rows: Sequence[sqlite3.Row], fee_rate: Decimal, slippage_rate: Decimal
) -> Dict[str, Any]:
    directional = [
        row for row in rows if row["actual_direction"] in {"UP", "DOWN"}
    ]
    gross = Decimal("0")
    costs = Decimal("0")
    net = Decimal("0")
    for row in directional:
        row_gross, row_costs, row_net = _hypothetical_pnl(
            row, fee_rate, slippage_rate
        )
        gross += row_gross
        costs += row_costs
        net += row_net
    wins = sum(int(row["correct"] or 0) for row in directional)
    return {
        "sample": len(directional),
        "hits": wins,
        "hit_rate": wins / len(directional) if directional else None,
        "gross_pnl_usd": float(gross),
        "modeled_cost_usd": float(costs),
        "net_pnl_usd": float(net),
        "net_expectancy_usd": float(net / len(directional))
        if directional
        else None,
    }


def _baseline_metrics(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    ordered = sorted(rows, key=lambda row: int(row["due_at"]))
    model_errors = []
    coin_errors = []
    drift_errors = []
    prior_up = 0
    prior_total = 0
    for row in ordered:
        if row["actual_direction"] not in {"UP", "DOWN"}:
            continue
        observed = 1.0 if row["actual_direction"] == "UP" else 0.0
        forecast = float(row["prob_up"])
        # Laplace smoothing makes this deterministic from the first cycle and
        # ensures the drift forecast uses only previously settled directions.
        drift_forecast = (prior_up + 1.0) / (prior_total + 2.0)
        model_errors.append((forecast - observed) ** 2)
        coin_errors.append((0.5 - observed) ** 2)
        drift_errors.append((drift_forecast - observed) ** 2)
        prior_up += int(observed)
        prior_total += 1
    if not model_errors:
        return {
            "sample": 0,
            "model_brier": None,
            "coin_flip_brier": None,
            "prior_drift_brier": None,
            "skill_vs_coin_flip": None,
            "skill_vs_prior_drift": None,
        }
    model = statistics.fmean(model_errors)
    coin = statistics.fmean(coin_errors)
    drift = statistics.fmean(drift_errors)
    return {
        "sample": len(model_errors),
        "model_brier": model,
        "coin_flip_brier": coin,
        "prior_drift_brier": drift,
        "skill_vs_coin_flip": 1.0 - model / coin if coin > 0 else None,
        "skill_vs_prior_drift": 1.0 - model / drift if drift > 0 else None,
    }


def build_economic_report(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    horizons: Mapping[str, int],
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> Dict[str, Any]:
    """Return aggregate-only evidence; never expose thresholds per decision."""
    report: Dict[str, Any] = {
        "policy_version": ECONOMIC_EVIDENCE_VERSION,
        "sample_minimum": PRIMARY_SAMPLE_MINIMUM,
        "primary_definition": "BOUNDARY_ISSUED_PROSPECTIVE_ONLY",
        "horizons": {},
    }
    for horizon in horizons:
        rows = conn.execute(
            """
            SELECT * FROM price_calls
            WHERE symbol = ? AND horizon_label = ? AND legacy = 0
              AND voided = 0 AND settled_at IS NOT NULL
            ORDER BY due_at
            """,
            (symbol, horizon),
        ).fetchall()
        classified = [
            row
            for row in rows
            if row["evidence_policy_version"] == ECONOMIC_EVIDENCE_VERSION
        ]
        primary = [
            row for row in classified if int(row["primary_benchmark_eligible"] or 0)
        ]
        exclusions: Dict[str, int] = {}
        for row in classified:
            code = row["primary_exclusion_code"]
            if code:
                exclusions[str(code)] = exclusions.get(str(code), 0) + 1
        provenance = {}
        for method in ("STREAM_KLINE", "REST_RECOVERY", "STATE_RECONCILIATION"):
            selected = [row for row in primary if row["settlement_method"] == method]
            if selected:
                metrics = _sample_metrics(selected, fee_rate, slippage_rate)
                metrics["median_delivery_latency_ms"] = _percentile(
                    [float(row["settlement_latency_ms"] or 0) for row in selected],
                    0.5,
                )
                provenance[method] = metrics
        # Lead time is a primary-benchmark metric. Including late-issued calls
        # silently depresses it and makes a healthy boundary scheduler look
        # delayed, even though those calls are explicitly excluded everywhere
        # else in this report.
        leads = [
            float(row["forecast_lead_ms"])
            for row in primary
            if row["forecast_lead_ms"] is not None
        ]
        executed = [
            row for row in primary if row["economic_action"] in {"UP", "DOWN"}
        ]
        report["horizons"][horizon] = {
            "all_settled_cycles": len(rows),
            "pre_policy_preserved_cycles": len(rows) - len(classified),
            "classified_cycles": len(classified),
            "primary_eligible_cycles": len(primary),
            "primary_excluded_cycles": len(classified) - len(primary),
            "exclusion_categories": exclusions,
            "sample_status": "OBSERVED"
            if len(primary) >= PRIMARY_SAMPLE_MINIMUM
            else "INSUFFICIENT_SAMPLE",
            "lead_time_ms": {
                "median": _percentile(leads, 0.5),
                "p05": _percentile(leads, 0.05),
                "p95": _percentile(leads, 0.95),
                "sample": len(leads),
                "scope": "PRIMARY_ONLY",
            },
            "primary_signal": _sample_metrics(primary, fee_rate, slippage_rate),
            "probability_benchmarks": _baseline_metrics(primary),
            "settlement_provenance": provenance,
            "economic_actions": {
                "eligible": len(executed),
                "abstained": len(primary) - len(executed),
                "participation_rate": len(executed) / len(primary)
                if primary
                else None,
                "eligible_signal": _sample_metrics(
                    executed, fee_rate, slippage_rate
                ),
            },
        }
    return report
