"""Cost-aware, Binance-cycle strategy tournament.

This module is deliberately separate from the verified price-call ledger.  It
consumes the same observed entries and official closed klines, but every
candidate owns an independent shadow-paper decision and outcome record.  No
exchange orders are submitted and no candidate can become a champion until it
passes all statistical, economic, stability, risk, and reliability gates.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from webapp.economic_evidence import (
    ECONOMIC_EVIDENCE_VERSION,
    assess_issue_timing,
)
from webapp.market_state import (
    MARKET_STATE_VERSION,
    ClosedCandle,
    MarketStateAssessment,
    get_market_state,
)


# Strategy rules and promotion gates are unchanged in this observability-only
# release, so retain the policy identity and its evaluation/champion lineage.
TOURNAMENT_VERSION = "quant-tournament-v1.3.0"
STARTING_EQUITY = Decimal("50.00")
STAKE = Decimal("1.00")
MONEY_QUANTUM = Decimal("0.00000001")
REGIMES = (
    "trending_up",
    "trending_down",
    "ranging",
    "breakout_up",
    "breakout_down",
    "high_volatility",
    "low_volatility",
    "unclassified",
)
ACTIONS = ("UP", "DOWN", "NO_TRADE")


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, value)


MIN_ELIGIBLE_TRADES = _env_int("QUANT_MIN_ELIGIBLE_TRADES", 300, 30)
MIN_WALK_FORWARD_FOLDS = _env_int("QUANT_MIN_WALK_FORWARD_FOLDS", 3, 2)
FOLD_SIZE = _env_int("QUANT_WALK_FORWARD_FOLD_SIZE", 50, 10)
MIN_PROFITABLE_FOLD_RATE = _env_float(
    "QUANT_MIN_PROFITABLE_FOLD_RATE", 0.60, 0.50
)
MAX_DRAWDOWN_PCT = _env_float("QUANT_MAX_DRAWDOWN_PCT", 10.0, 0.10)
MIN_RELIABILITY = min(1.0, _env_float("QUANT_MIN_RELIABILITY", 0.95, 0.50))
CHALLENGER_MARGIN_POINTS = _env_float(
    "QUANT_CHALLENGER_MARGIN_POINTS", 5.0, 0.0
)
CHALLENGER_CONFIRMATIONS = _env_int("QUANT_CHALLENGER_CONFIRMATIONS", 3, 1)
EVALUATE_EVERY_CYCLES = _env_int("QUANT_EVALUATE_EVERY_CYCLES", 25, 1)

COMPOSITE_WEIGHTS = {
    "expectancy": 0.40,
    "walk_forward_stability": 0.20,
    "profit_factor": 0.15,
    "drawdown_control": 0.10,
    "calibration": 0.10,
    "reliability": 0.05,
}


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("price must be finite and positive")
    return number


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    bounded = _clip(value, -30.0, 30.0)
    return 1.0 / (1.0 + math.exp(-bounded))


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _ema(values: Sequence[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * _clip(fraction, 0.0, 1.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class FeatureSnapshot:
    current_price: float
    tick_return_bps: float
    tick_volatility_bps: float
    tick_zscore: float
    orderbook_pressure: float
    spread_bps: float
    candle_count: int
    ema_fast: float
    ema_slow: float
    trend_bps: float
    atr_bps: float
    realized_volatility_bps: float
    previous_high: Optional[float]
    previous_low: Optional[float]
    rsi_14: Optional[float]
    macd_bps: Optional[float]
    bollinger_zscore: Optional[float]
    time_series_momentum_bps: Optional[float]
    candle_pattern_signal: int
    ml_probability_up: Optional[float]
    ml_training_samples: int
    ml_feature_importance: Optional[Dict[str, float]]
    ml_model_fingerprint: Optional[str]
    ml_trained_at_ms: Optional[int]
    ml_training_start_ms: Optional[int]
    ml_training_end_ms: Optional[int]
    ml_label_up_rate: Optional[float]
    regime: str
    market_state_version: str
    data_state: str
    regime_confidence: float
    missing_candle_intervals: int


@dataclass(frozen=True)
class CandidateDecision:
    action: str
    probability_up: float
    confidence: float
    expected_move_bps: float
    reason: str
    reason_code: str = "SIGNAL"

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unsupported action: {self.action}")
        if not 0.0 <= self.probability_up <= 1.0:
            raise ValueError("probability_up must be between zero and one")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    version: str


CANDIDATES = (
    CandidateSpec("cost_filtered_price_call", "1.0.0"),
    CandidateSpec("rsi_mean_reversion", "1.0.0"),
    CandidateSpec("macd", "1.0.0"),
    CandidateSpec("bollinger_bands", "1.0.0"),
    CandidateSpec("time_series_momentum", "1.0.0"),
    CandidateSpec("sma_crossover", "1.0.0"),
    CandidateSpec("candlestick_patterns", "1.0.0"),
    CandidateSpec("ml_ensemble", "1.0.0"),
    CandidateSpec("micro_momentum", "1.0.0"),
    CandidateSpec("orderbook_imbalance", "1.0.0"),
    CandidateSpec("micro_mean_reversion", "1.0.0"),
    CandidateSpec("regime_trend_following", "1.0.0"),
    CandidateSpec("volatility_breakout", "1.0.0"),
)


@dataclass
class _PathState:
    entry: Decimal
    high: Decimal
    low: Decimal
    decision_ms: int
    tracking_started_ms: int
    observations: int = 0

    def update(self, price: Decimal, trade_ms: int) -> None:
        if int(trade_ms) < self.decision_ms:
            return
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.observations += 1


class QuantStrategyTournament:
    """Records candidate evidence and applies fail-closed promotion policy."""

    def __init__(
        self,
        db_path: str,
        *,
        fee_rate: Decimal = Decimal("0.001"),
        slippage_rate: Decimal = Decimal("0.0002"),
        clock_ms=None,
    ) -> None:
        self.db_path = db_path
        self.fee_rate = _decimal(fee_rate)
        self.slippage_rate = _decimal(slippage_rate)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._paths: Dict[Tuple[str, str, int], _PathState] = {}
        self._path_lock = threading.Lock()
        self.market_state = get_market_state(self.db_path)
        self._init_db()
        self._restore_open_paths()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS quant_market_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    cycle_start_ms INTEGER NOT NULL,
                    due_at_ms INTEGER NOT NULL,
                    open_text TEXT NOT NULL,
                    high_text TEXT NOT NULL,
                    low_text TEXT NOT NULL,
                    close_text TEXT NOT NULL,
                    settled_at_ms INTEGER NOT NULL,
                    settlement_method TEXT NOT NULL,
                    UNIQUE(symbol, horizon_label, due_at_ms)
                );

                CREATE TABLE IF NOT EXISTS quant_candidate_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    price_call_id INTEGER,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    cycle_start_ms INTEGER NOT NULL,
                    due_at_ms INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    candidate_name TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    tournament_version TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('UP','DOWN','NO_TRADE')),
                    entry_price_text TEXT NOT NULL,
                    probability_up REAL NOT NULL,
                    confidence REAL NOT NULL,
                    expected_move_bps REAL NOT NULL,
                    decision_reason TEXT NOT NULL,
                    feature_snapshot TEXT NOT NULL,
                    fee_rate_text TEXT NOT NULL,
                    slippage_rate_text TEXT NOT NULL,
                    UNIQUE(symbol, horizon_label, due_at_ms, candidate_name),
                    FOREIGN KEY(price_call_id) REFERENCES price_calls(id)
                );

                CREATE TABLE IF NOT EXISTS quant_candidate_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('SETTLED','VOID')),
                    settled_at_ms INTEGER NOT NULL,
                    actual_direction TEXT,
                    open_text TEXT,
                    high_text TEXT,
                    low_text TEXT,
                    close_text TEXT,
                    mfe_bps REAL,
                    mae_bps REAL,
                    gross_return_bps REAL,
                    cost_bps REAL,
                    net_return_bps REAL,
                    gross_pnl_text TEXT,
                    fee_cost_text TEXT,
                    slippage_cost_text TEXT,
                    net_pnl_text TEXT,
                    correct INTEGER,
                    eligible_trade INTEGER NOT NULL,
                    excursion_source TEXT,
                    settlement_method TEXT,
                    void_reason TEXT,
                    FOREIGN KEY(decision_id) REFERENCES quant_candidate_decisions(id)
                );

                CREATE TABLE IF NOT EXISTS quant_candidate_equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    candidate_name TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    balance_before_text TEXT NOT NULL,
                    balance_after_text TEXT NOT NULL,
                    net_pnl_text TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES quant_candidate_decisions(id)
                );

                CREATE TABLE IF NOT EXISTS quant_tournament_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluated_at_ms INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    best_candidate TEXT,
                    best_score REAL,
                    qualified_count INTEGER NOT NULL,
                    report TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quant_champion_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_at_ms INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('PROMOTED','DEMOTED','REPLACED')),
                    champion_name TEXT,
                    previous_champion TEXT,
                    champion_confidence REAL,
                    reason TEXT NOT NULL,
                    metrics_snapshot TEXT NOT NULL,
                    tournament_run_id INTEGER NOT NULL,
                    FOREIGN KEY(tournament_run_id) REFERENCES quant_tournament_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_quant_decisions_contract
                    ON quant_candidate_decisions(symbol, horizon_label, regime, candidate_name, due_at_ms);
                CREATE INDEX IF NOT EXISTS idx_quant_outcomes_status
                    ON quant_candidate_outcomes(status, eligible_trade, decision_id);
                CREATE INDEX IF NOT EXISTS idx_quant_runs_contract
                    ON quant_tournament_runs(symbol, horizon_label, regime, evaluated_at_ms);
                CREATE INDEX IF NOT EXISTS idx_quant_champion_contract
                    ON quant_champion_events(symbol, horizon_label, regime, event_at_ms);

                CREATE TRIGGER IF NOT EXISTS quant_decisions_no_update
                BEFORE UPDATE ON quant_candidate_decisions
                BEGIN SELECT RAISE(ABORT, 'quant decisions are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS quant_decisions_no_delete
                BEFORE DELETE ON quant_candidate_decisions
                BEGIN SELECT RAISE(ABORT, 'quant decisions are permanent'); END;
                CREATE TRIGGER IF NOT EXISTS quant_outcomes_no_update
                BEFORE UPDATE ON quant_candidate_outcomes
                BEGIN SELECT RAISE(ABORT, 'quant outcomes are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS quant_outcomes_no_delete
                BEFORE DELETE ON quant_candidate_outcomes
                BEGIN SELECT RAISE(ABORT, 'quant outcomes are permanent'); END;
                CREATE TRIGGER IF NOT EXISTS quant_champion_events_no_update
                BEFORE UPDATE ON quant_champion_events
                BEGIN SELECT RAISE(ABORT, 'champion history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS quant_champion_events_no_delete
                BEFORE DELETE ON quant_champion_events
                BEGIN SELECT RAISE(ABORT, 'champion history is permanent'); END;
                """
            )
            decision_columns = {
                str(row[1]) for row in conn.execute(
                    "PRAGMA table_info(quant_candidate_decisions)"
                )
            }
            additions = {
                "market_state_snapshot_id": "INTEGER",
                "market_state_version": "TEXT",
                "data_state": "TEXT",
                "regime_confidence": "REAL",
                "no_trade_code": "TEXT",
                "evidence_policy_version": "TEXT",
                "timing_class": "TEXT",
                "issue_delay_ms": "INTEGER",
                "forecast_lead_ms": "INTEGER",
                "forecast_lead_ratio": "REAL",
                "primary_benchmark_eligible": "INTEGER",
                "primary_exclusion_code": "TEXT",
                "model_fingerprint": "TEXT",
                "model_trained_at_ms": "INTEGER",
                "model_training_start_ms": "INTEGER",
                "model_training_end_ms": "INTEGER",
                "model_training_samples": "INTEGER",
                "model_label_up_rate": "REAL",
            }
            for name, declaration in additions.items():
                if name not in decision_columns:
                    conn.execute(
                        f'ALTER TABLE quant_candidate_decisions ADD COLUMN "{name}" {declaration}'
                    )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_quant_decisions_primary_evidence
                ON quant_candidate_decisions (
                    symbol, horizon_label, evidence_policy_version,
                    primary_benchmark_eligible, candidate_name, due_at_ms
                )
                """
            )
            conn.commit()

    def _restore_open_paths(self) -> None:
        now_ms = int(self._clock_ms())
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, horizon_label, due_at_ms,
                       MIN(decision_at_ms) AS decision_at_ms,
                       MIN(entry_price_text) AS entry_price_text
                FROM quant_candidate_decisions d
                WHERE NOT EXISTS (
                    SELECT 1 FROM quant_candidate_outcomes o
                    WHERE o.decision_id = d.id
                )
                GROUP BY symbol, horizon_label, due_at_ms
                """
            ).fetchall()
        with self._path_lock:
            for row in rows:
                entry = _decimal(row["entry_price_text"])
                self._paths[(row["symbol"], row["horizon_label"], int(row["due_at_ms"]))] = _PathState(
                    entry=entry,
                    high=entry,
                    low=entry,
                    decision_ms=int(row["decision_at_ms"]),
                    tracking_started_ms=now_ms,
                )

    def observe_trade(self, symbol: str, price_text: str, trade_ms: int) -> None:
        price = _decimal(price_text)
        key_prefix = str(symbol).upper()
        with self._path_lock:
            for (candidate_symbol, _horizon, due_at), state in self._paths.items():
                if candidate_symbol == key_prefix and int(trade_ms) < due_at:
                    state.update(price, int(trade_ms))

    def _feature_snapshot(
        self,
        entry: Decimal,
        orderbook_depth: Mapping[str, Any],
        recent_ticks: Sequence[Mapping[str, Any]],
        market_state: MarketStateAssessment,
    ) -> FeatureSnapshot:
        prices = [float(_decimal(tick["price"])) for tick in recent_ticks]
        current = float(entry)
        tick_return = 0.0
        if len(prices) >= 2 and prices[0] > 0:
            tick_return = math.log(prices[-1] / prices[0]) * 10_000.0
        tick_returns = [
            math.log(prices[index] / prices[index - 1]) * 10_000.0
            for index in range(1, len(prices))
            if prices[index] > 0 and prices[index - 1] > 0
        ]
        tick_vol = _std(tick_returns)
        price_std = statistics.pstdev(prices) if len(prices) >= 2 else 0.0
        zscore = (prices[-1] - _mean(prices)) / price_std if price_std > 0 else 0.0

        bids = orderbook_depth.get("bids", []) or []
        asks = orderbook_depth.get("asks", []) or []
        bid_quantity = sum(float(row[1]) for row in bids if len(row) >= 2)
        ask_quantity = sum(float(row[1]) for row in asks if len(row) >= 2)
        pressure = bid_quantity / (bid_quantity + ask_quantity) if bid_quantity + ask_quantity > 0 else 0.5
        best_bid = max((float(row[0]) for row in bids if len(row) >= 2), default=current)
        best_ask = min((float(row[0]) for row in asks if len(row) >= 2), default=current)
        midpoint = (best_bid + best_ask) / 2.0
        spread_bps = max(0.0, (best_ask - best_bid) / midpoint * 10_000.0) if midpoint > 0 else 0.0

        candles = market_state.candles
        closes = [float(row["close_text"]) for row in candles]
        opens = [float(row["open_text"]) for row in candles]
        highs = [float(row["high_text"]) for row in candles]
        lows = [float(row["low_text"]) for row in candles]
        fast = _ema(closes[-10:], 5) if closes else current
        slow = _ema(closes[-30:], 20) if closes else current
        trend_bps = (fast - slow) / slow * 10_000.0 if slow > 0 else 0.0
        true_ranges = []
        for index, close in enumerate(closes):
            previous_close = closes[index - 1] if index else close
            true_ranges.append(
                max(
                    highs[index] - lows[index],
                    abs(highs[index] - previous_close),
                    abs(lows[index] - previous_close),
                ) / previous_close * 10_000.0
            )
        atr = _mean(true_ranges[-14:])
        close_returns = [
            math.log(closes[index] / closes[index - 1]) * 10_000.0
            for index in range(1, len(closes))
            if closes[index] > 0 and closes[index - 1] > 0
        ]
        realized = _std(close_returns[-20:])
        rsi = self._rsi(closes, 14)
        macd_bps = (
            (_ema(closes[-40:], 12) - _ema(closes[-60:], 26)) / closes[-1] * 10_000.0
            if len(closes) >= 26 and closes[-1] > 0 else None
        )
        bollinger_zscore = None
        if len(closes) >= 20:
            centre = _mean(closes[-20:])
            deviation = statistics.pstdev(closes[-20:])
            if deviation > 0:
                bollinger_zscore = (current - centre) / deviation
        time_series_momentum = (
            (current / closes[-20] - 1.0) * 10_000.0
            if len(closes) >= 20 and closes[-20] > 0 else None
        )
        candle_pattern_signal = self._engulfing_signal(opens, closes)
        close_times = [int(row["close_time_ms"]) for row in candles]
        (
            ml_probability,
            ml_samples,
            ml_importance,
            ml_fingerprint,
            ml_training_start,
            ml_training_end,
            ml_label_up_rate,
        ) = self._rolling_logistic_probability(closes, current, close_times)
        return FeatureSnapshot(
            current_price=current,
            tick_return_bps=tick_return,
            tick_volatility_bps=tick_vol,
            tick_zscore=zscore,
            orderbook_pressure=pressure,
            spread_bps=spread_bps,
            candle_count=len(candles),
            ema_fast=fast,
            ema_slow=slow,
            trend_bps=trend_bps,
            atr_bps=atr,
            realized_volatility_bps=realized,
            previous_high=highs[-1] if highs else None,
            previous_low=lows[-1] if lows else None,
            rsi_14=rsi,
            macd_bps=macd_bps,
            bollinger_zscore=bollinger_zscore,
            time_series_momentum_bps=time_series_momentum,
            candle_pattern_signal=candle_pattern_signal,
            ml_probability_up=ml_probability,
            ml_training_samples=ml_samples,
            ml_feature_importance=ml_importance,
            ml_model_fingerprint=ml_fingerprint,
            ml_trained_at_ms=(
                int(market_state.decision_at_ms) if ml_fingerprint else None
            ),
            ml_training_start_ms=ml_training_start,
            ml_training_end_ms=ml_training_end,
            ml_label_up_rate=ml_label_up_rate,
            regime=market_state.regime,
            market_state_version=MARKET_STATE_VERSION,
            data_state=market_state.data_state,
            regime_confidence=market_state.confidence,
            missing_candle_intervals=market_state.missing_intervals,
        )

    @staticmethod
    def _rsi(closes: Sequence[float], period: int) -> Optional[float]:
        if len(closes) <= period:
            return None
        changes = [closes[index] - closes[index - 1] for index in range(len(closes) - period, len(closes))]
        average_gain = _mean([max(change, 0.0) for change in changes])
        average_loss = _mean([max(-change, 0.0) for change in changes])
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        relative_strength = average_gain / average_loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    @staticmethod
    def _engulfing_signal(opens: Sequence[float], closes: Sequence[float]) -> int:
        if len(opens) < 2 or len(closes) < 2:
            return 0
        previous_open, previous_close = opens[-2], closes[-2]
        current_open, current_close = opens[-1], closes[-1]
        bullish = (
            previous_close < previous_open
            and current_close > current_open
            and current_open <= previous_close
            and current_close >= previous_open
        )
        bearish = (
            previous_close > previous_open
            and current_close < current_open
            and current_open >= previous_close
            and current_close <= previous_open
        )
        return 1 if bullish else -1 if bearish else 0

    @staticmethod
    def _rolling_logistic_probability(
        closes: Sequence[float],
        current_price: float,
        close_times: Optional[Sequence[int]] = None,
    ) -> Tuple[
        Optional[float], int, Optional[Dict[str, float]], Optional[str],
        Optional[int], Optional[int], Optional[float],
    ]:
        """Small regularized model trained only on earlier closed candles.

        Each row uses information available at its timestamp and predicts the
        following close.  The current prediction is therefore forward-only;
        no future candle or current-cycle outcome enters training.
        """
        if len(closes) < 70:
            usable_samples = max(0, len(closes) - 21)
            return (
                None, usable_samples, None, None, None, None, None
            )
        if close_times is not None and len(close_times) != len(closes):
            raise ValueError("close_times must align exactly with closes")
        values = np.asarray(closes, dtype=float)
        rows: List[List[float]] = []
        targets: List[float] = []
        for index in range(20, len(values) - 1):
            recent_returns = np.diff(np.log(values[index - 10:index + 1])) * 10_000.0
            sma5 = float(np.mean(values[index - 4:index + 1]))
            sma20 = float(np.mean(values[index - 19:index + 1]))
            rows.append([
                float(recent_returns[-1]),
                float(np.mean(recent_returns[-3:])),
                float(np.mean(recent_returns)),
                float(np.std(recent_returns)),
                (sma5 / sma20 - 1.0) * 10_000.0,
            ])
            targets.append(float(values[index + 1] > values[index]))
        if len(rows) < 40 or len(set(targets)) < 2:
            return None, len(rows), None, None, None, None, (
                _mean(targets) if targets else None
            )
        matrix = np.asarray(rows, dtype=float)
        target = np.asarray(targets, dtype=float)
        centre = matrix.mean(axis=0)
        scale = matrix.std(axis=0)
        scale[scale < 1e-9] = 1.0
        standardized = (matrix - centre) / scale
        design = np.column_stack([np.ones(len(standardized)), standardized])
        weights = np.zeros(design.shape[1], dtype=float)
        regularization = 0.05
        for _ in range(180):
            logits = np.clip(design @ weights, -30.0, 30.0)
            probabilities = 1.0 / (1.0 + np.exp(-logits))
            gradient = design.T @ (probabilities - target) / len(target)
            gradient[1:] += regularization * weights[1:]
            weights -= 0.08 * gradient
        augmented = np.append(values, float(current_price))
        recent_returns = np.diff(np.log(augmented[-11:])) * 10_000.0
        current_row = np.asarray([
            float(recent_returns[-1]),
            float(np.mean(recent_returns[-3:])),
            float(np.mean(recent_returns)),
            float(np.std(recent_returns)),
            (float(np.mean(augmented[-5:])) / float(np.mean(augmented[-20:])) - 1.0) * 10_000.0,
        ])
        current_design = np.concatenate([[1.0], (current_row - centre) / scale])
        probability = _sigmoid(float(current_design @ weights))
        feature_names = (
            "return_1",
            "mean_return_3",
            "mean_return_10",
            "volatility_10",
            "sma_5_vs_20",
        )
        absolute = np.abs(weights[1:])
        total = float(absolute.sum())
        importance = {
            name: float(value / total) if total > 0 else 0.0
            for name, value in zip(feature_names, absolute)
        }
        training_start = int(close_times[20]) if close_times is not None else None
        training_end = int(close_times[-1]) if close_times is not None else None
        identity = {
            "algorithm": "regularized-logistic-v1",
            "weights": [round(float(value), 12) for value in weights],
            "centre": [round(float(value), 12) for value in centre],
            "scale": [round(float(value), 12) for value in scale],
            "samples": len(rows),
            "label_up_rate": round(float(target.mean()), 12),
            "training_start_ms": training_start,
            "training_end_ms": training_end,
        }
        fingerprint = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
        return (
            probability,
            len(rows),
            importance,
            fingerprint,
            training_start,
            training_end,
            float(target.mean()),
        )

    @staticmethod
    def _classify_regime(
        trend_bps: float,
        atr_bps: float,
        true_ranges: Sequence[float],
        candle_count: int,
    ) -> str:
        if candle_count < 20:
            return "unclassified"
        history = list(true_ranges[:-1] or true_ranges)
        high_threshold = _percentile(history, 0.75)
        low_threshold = _percentile(history, 0.25)
        if high_threshold > 0 and atr_bps >= high_threshold * 1.15:
            return "high_volatility"
        trend_threshold = max(8.0, atr_bps * 0.45)
        if trend_bps >= trend_threshold:
            return "trending_up"
        if trend_bps <= -trend_threshold:
            return "trending_down"
        if low_threshold > 0 and atr_bps <= low_threshold * 0.85:
            return "low_volatility"
        return "ranging"

    @staticmethod
    def _no_trade(reason: str, reason_code: Optional[str] = None) -> CandidateDecision:
        return CandidateDecision(
            "NO_TRADE", 0.5, 0.0, 0.0, reason,
            reason_code or QuantStrategyTournament._no_trade_category(reason),
        )

    @staticmethod
    def _no_trade_category(reason: str) -> str:
        value = str(reason).lower()
        if "requires" in value or "warm-up" in value or "warmup" in value:
            return "INSUFFICIENT_HISTORY"
        if "cost" in value or "movement" in value:
            return "COST_GATE"
        if "regime" in value or "trend/high-volatility" in value:
            return "REGIME_FILTER"
        if "model" in value:
            return "MODEL_NOT_READY"
        if "spread" in value or "order-book" in value or "quoted-depth" in value:
            return "MARKET_QUALITY"
        if "pattern" in value:
            return "NO_PATTERN"
        if "confirm" in value or "contradict" in value:
            return "CONFIRMATION_MISSING"
        return "SIGNAL_BELOW_THRESHOLD"

    def _candidate_decision(
        self,
        candidate: CandidateSpec,
        features: FeatureSnapshot,
        base_probability_up: float,
        round_trip_cost_bps: float,
    ) -> CandidateDecision:
        name = candidate.name
        minimum_move = round_trip_cost_bps * 1.20

        if name == "cost_filtered_price_call":
            probability = _clip(float(base_probability_up), 0.0, 1.0)
            confidence = abs(probability - 0.5) * 2.0
            expected_move = max(features.atr_bps, abs(features.tick_return_bps)) * confidence
            if confidence < 0.12:
                return self._no_trade("model edge below the pre-cost participation floor")
            if expected_move <= minimum_move:
                return self._no_trade("estimated movement does not clear modeled round-trip costs")
            action = "UP" if probability > 0.5 else "DOWN"
            return CandidateDecision(action, probability, confidence, expected_move, "cost-filtered price-call probability")

        if name == "rsi_mean_reversion":
            if features.rsi_14 is None:
                return self._no_trade("requires 15 completed same-horizon candles")
            if features.regime in {"trending_up", "trending_down", "high_volatility"}:
                return self._no_trade("RSI mean reversion disabled in trend/high-volatility regime")
            if features.rsi_14 <= 30.0:
                strength = (30.0 - features.rsi_14) / 30.0
                return CandidateDecision("UP", 0.5 + 0.4 * strength, strength, features.atr_bps, "oversold 14-period RSI in a non-trending regime")
            if features.rsi_14 >= 70.0:
                strength = (features.rsi_14 - 70.0) / 30.0
                return CandidateDecision("DOWN", 0.5 - 0.4 * strength, strength, features.atr_bps, "overbought 14-period RSI in a non-trending regime")
            return self._no_trade("RSI remains inside the 30/70 participation band")

        if name == "macd":
            if features.macd_bps is None:
                return self._no_trade("requires 26 completed same-horizon candles")
            threshold = max(2.0, features.atr_bps * 0.10)
            if abs(features.macd_bps) < threshold:
                return self._no_trade("MACD spread is below its volatility-scaled threshold")
            probability = _sigmoid(features.macd_bps / max(threshold, 1.0))
            return CandidateDecision("UP" if features.macd_bps > 0 else "DOWN", probability, _clip(abs(features.macd_bps) / max(features.atr_bps, 1.0), 0.0, 1.0), max(abs(features.macd_bps), features.atr_bps), "12/26 EMA spread")

        if name == "bollinger_bands":
            if features.bollinger_zscore is None:
                return self._no_trade("requires 20 completed same-horizon candles")
            if features.regime in {"trending_up", "trending_down", "high_volatility"}:
                return self._no_trade("Bollinger reversion disabled in trend/high-volatility regime")
            if abs(features.bollinger_zscore) < 2.0:
                return self._no_trade("price remains inside the two-standard-deviation band")
            signal = -features.bollinger_zscore
            probability = _sigmoid(signal)
            return CandidateDecision("UP" if signal > 0 else "DOWN", probability, _clip((abs(signal) - 1.5) / 2.0, 0.0, 1.0), abs(signal) * max(features.realized_volatility_bps, 1.0), "two-standard-deviation Bollinger reversion")

        if name == "time_series_momentum":
            if features.time_series_momentum_bps is None:
                return self._no_trade("requires 20 completed same-horizon candles")
            threshold = max(minimum_move, features.atr_bps)
            if abs(features.time_series_momentum_bps) < threshold:
                return self._no_trade("20-candle momentum does not clear volatility and cost thresholds")
            probability = _sigmoid(features.time_series_momentum_bps / max(threshold, 1.0))
            return CandidateDecision("UP" if features.time_series_momentum_bps > 0 else "DOWN", probability, _clip(abs(features.time_series_momentum_bps) / (3.0 * threshold), 0.0, 1.0), abs(features.time_series_momentum_bps), "20-candle time-series momentum")

        if name == "sma_crossover":
            if features.candle_count < 30:
                return self._no_trade("requires 30 completed same-horizon candles")
            spread = features.trend_bps
            threshold = max(5.0, features.atr_bps * 0.35)
            if abs(spread) < threshold:
                return self._no_trade("fast/slow average spread is below its volatility threshold")
            probability = _sigmoid(spread / max(threshold, 1.0))
            return CandidateDecision("UP" if spread > 0 else "DOWN", probability, _clip(abs(spread) / (3.0 * threshold), 0.0, 1.0), max(abs(spread), features.atr_bps), "5/20 exponential moving-average spread")

        if name == "candlestick_patterns":
            if features.candle_pattern_signal == 0:
                return self._no_trade("no completed bullish or bearish engulfing pattern")
            direction = features.candle_pattern_signal
            probability = 0.62 if direction > 0 else 0.38
            return CandidateDecision("UP" if direction > 0 else "DOWN", probability, 0.24, features.atr_bps, "completed-candle engulfing pattern")

        if name == "ml_ensemble":
            if features.ml_probability_up is None:
                return self._no_trade(
                    "rolling model warm-up: "
                    f"{features.ml_training_samples} usable samples; "
                    "70 closed candles and both outcome labels are required",
                    "MODEL_WARMING",
                )
            trend_probability = _sigmoid(
                features.trend_bps / max(features.atr_bps, 1.0)
            )
            probability = 0.75 * features.ml_probability_up + 0.25 * trend_probability
            confidence = abs(probability - 0.5) * 2.0
            if confidence < 0.20:
                return self._no_trade(
                    "regularized rolling model is ready but probability remains inside 40/60",
                    "MODEL_READY_LOW_EDGE",
                )
            expected_move = features.atr_bps * confidence
            return CandidateDecision("UP" if probability > 0.5 else "DOWN", probability, confidence, expected_move, f"regularized rolling logistic + trend ensemble trained on {features.ml_training_samples} forward-only samples")

        if name == "micro_momentum":
            signal = features.tick_return_bps
            threshold = max(2.0, features.tick_volatility_bps * 1.5, minimum_move * 0.20)
            pressure_confirms = (
                signal > 0 and features.orderbook_pressure >= 0.52
            ) or (signal < 0 and features.orderbook_pressure <= 0.48)
            if abs(signal) < threshold or not pressure_confirms:
                return self._no_trade("tick momentum lacks magnitude or order-book confirmation")
            probability = _sigmoid(signal / max(threshold, 1.0))
            return CandidateDecision(
                "UP" if signal > 0 else "DOWN",
                probability,
                _clip(abs(signal) / (3.0 * threshold), 0.0, 1.0),
                abs(signal),
                "recent trade momentum confirmed by quoted depth",
            )

        if name == "orderbook_imbalance":
            imbalance = features.orderbook_pressure - 0.5
            if features.spread_bps > max(5.0, minimum_move * 0.35):
                return self._no_trade("spread is too wide for the imbalance signal")
            if abs(imbalance) < 0.10:
                return self._no_trade("quoted-depth imbalance is below 60/40")
            score = imbalance * 12.0
            probability = _sigmoid(score)
            return CandidateDecision(
                "UP" if imbalance > 0 else "DOWN",
                probability,
                _clip(abs(imbalance) / 0.30, 0.0, 1.0),
                max(abs(features.tick_return_bps), abs(imbalance) * 100.0),
                "top-of-book quantity imbalance",
            )

        if name == "micro_mean_reversion":
            if features.regime in {"trending_up", "trending_down", "high_volatility"}:
                return self._no_trade("mean reversion disabled in trend/high-volatility regime")
            if abs(features.tick_zscore) < 1.35:
                return self._no_trade("micro-price deviation is not extreme")
            signal = -features.tick_zscore
            probability = _sigmoid(signal)
            return CandidateDecision(
                "UP" if signal > 0 else "DOWN",
                probability,
                _clip((abs(features.tick_zscore) - 1.0) / 2.0, 0.0, 1.0),
                abs(features.tick_return_bps),
                "short-horizon price deviation from the observed trade mean",
            )

        if name == "regime_trend_following":
            if features.candle_count < 30:
                return self._no_trade("requires 30 completed same-horizon candles")
            if features.regime not in {"trending_up", "trending_down"}:
                return self._no_trade("trend candidate participates only in a validated trend regime")
            direction = 1.0 if features.regime == "trending_up" else -1.0
            if direction * features.tick_return_bps < -max(2.0, features.tick_volatility_bps):
                return self._no_trade("live micro-momentum contradicts the closed-candle trend")
            strength = abs(features.trend_bps) / max(features.atr_bps, 1.0)
            probability = _sigmoid(direction * min(strength, 5.0))
            return CandidateDecision(
                "UP" if direction > 0 else "DOWN",
                probability,
                _clip(strength / 3.0, 0.0, 1.0),
                abs(features.trend_bps),
                "same-horizon EMA trend with live confirmation",
            )

        if name == "volatility_breakout":
            if features.candle_count < 20 or features.previous_high is None or features.previous_low is None:
                return self._no_trade("requires 20 completed candles and a prior range")
            buffer_bps = max(1.0, features.spread_bps, minimum_move * 0.10)
            upper = features.previous_high * (1.0 + buffer_bps / 10_000.0)
            lower = features.previous_low * (1.0 - buffer_bps / 10_000.0)
            if features.current_price > upper:
                distance = (features.current_price / features.previous_high - 1.0) * 10_000.0
                probability = _sigmoid(distance / max(buffer_bps, 1.0))
                return CandidateDecision("UP", probability, _clip(distance / max(features.atr_bps, 1.0), 0.0, 1.0), max(distance, features.atr_bps), "break above prior completed-candle high")
            if features.current_price < lower:
                distance = (features.previous_low / features.current_price - 1.0) * 10_000.0
                probability = 1.0 - _sigmoid(distance / max(buffer_bps, 1.0))
                return CandidateDecision("DOWN", probability, _clip(distance / max(features.atr_bps, 1.0), 0.0, 1.0), max(distance, features.atr_bps), "break below prior completed-candle low")
            return self._no_trade("price remains inside the prior completed-candle range")

        raise KeyError(f"unknown candidate: {name}")

    def record_cycle_decisions(
        self,
        *,
        price_call_id: int,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        due_at_ms: int,
        decision_at_ms: int,
        entry_price_text: str,
        orderbook_depth: Mapping[str, Any],
        recent_ticks: Sequence[Mapping[str, Any]],
        base_probability_up: float,
        timing_policy_version: str = ECONOMIC_EVIDENCE_VERSION,
    ) -> int:
        symbol = str(symbol).upper()
        entry = _decimal(entry_price_text)
        market_state = self.market_state.assess_cycle(
            symbol=symbol,
            horizon=horizon,
            cycle_start_ms=int(cycle_start_ms),
            decision_at_ms=int(decision_at_ms),
        )
        market_state_snapshot_id = self.market_state.record_assessment(market_state)
        features = self._feature_snapshot(
            entry, orderbook_depth, recent_ticks, market_state
        )
        feature_json = _json(asdict(features))
        round_trip_cost_bps = float((self.fee_rate + self.slippage_rate) * Decimal("2") * Decimal("10000"))
        timing = assess_issue_timing(
            horizon_ms=int(due_at_ms) - int(cycle_start_ms),
            cycle_start_ms=int(cycle_start_ms),
            due_at_ms=int(due_at_ms),
            decision_at_ms=int(decision_at_ms),
        )
        if timing_policy_version != ECONOMIC_EVIDENCE_VERSION:
            primary_eligible = False
            timing_class = "PRE_POLICY"
            primary_exclusion_code = "PRE_POLICY_EVIDENCE"
            evidence_policy_version = str(timing_policy_version)
        else:
            primary_eligible = timing.primary_eligible
            timing_class = timing.timing_class
            primary_exclusion_code = timing.exclusion_code
            evidence_policy_version = ECONOMIC_EVIDENCE_VERSION
        inserted = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for candidate in CANDIDATES:
                decision = self._candidate_decision(
                    candidate, features, float(base_probability_up), round_trip_cost_bps
                )
                if (
                    decision.action != "NO_TRADE"
                    and decision.expected_move_bps <= round_trip_cost_bps * 1.20
                ):
                    decision = self._no_trade(
                        "candidate signal did not forecast enough movement to clear costs with a safety margin"
                    )
                if not primary_eligible:
                    decision = CandidateDecision(
                        action="NO_TRADE",
                        probability_up=decision.probability_up,
                        confidence=decision.confidence,
                        expected_move_bps=decision.expected_move_bps,
                        reason="decision excluded from the primary benchmark by point-in-time evidence policy",
                        reason_code="TIMING_INELIGIBLE",
                    )
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO quant_candidate_decisions (
                        price_call_id, symbol, horizon_label, cycle_start_ms,
                        due_at_ms, decision_at_ms, candidate_name,
                        candidate_version, tournament_version, regime, action,
                        entry_price_text, probability_up, confidence,
                        expected_move_bps, decision_reason, feature_snapshot,
                        fee_rate_text, slippage_rate_text,
                        market_state_snapshot_id, market_state_version,
                        data_state, regime_confidence, no_trade_code,
                        evidence_policy_version, timing_class,
                        issue_delay_ms, forecast_lead_ms,
                        forecast_lead_ratio, primary_benchmark_eligible,
                        primary_exclusion_code, model_fingerprint,
                        model_trained_at_ms, model_training_start_ms,
                        model_training_end_ms, model_training_samples,
                        model_label_up_rate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(price_call_id), symbol, horizon, int(cycle_start_ms),
                        int(due_at_ms), int(decision_at_ms), candidate.name,
                        candidate.version, TOURNAMENT_VERSION, features.regime,
                        decision.action, str(entry_price_text),
                        decision.probability_up, decision.confidence,
                        decision.expected_move_bps, decision.reason, feature_json,
                        str(self.fee_rate), str(self.slippage_rate),
                        int(market_state_snapshot_id), MARKET_STATE_VERSION,
                        market_state.data_state, market_state.confidence,
                        decision.reason_code if decision.action == "NO_TRADE" else None,
                        evidence_policy_version, timing_class,
                        timing.issue_delay_ms, timing.forecast_lead_ms,
                        timing.forecast_lead_ratio, int(primary_eligible),
                        primary_exclusion_code,
                        (
                            features.ml_model_fingerprint
                            if candidate.name == "ml_ensemble" else None
                        ),
                        (
                            features.ml_trained_at_ms
                            if candidate.name == "ml_ensemble" else None
                        ),
                        (
                            features.ml_training_start_ms
                            if candidate.name == "ml_ensemble" else None
                        ),
                        (
                            features.ml_training_end_ms
                            if candidate.name == "ml_ensemble" else None
                        ),
                        (
                            features.ml_training_samples
                            if candidate.name == "ml_ensemble" else None
                        ),
                        (
                            features.ml_label_up_rate
                            if candidate.name == "ml_ensemble" else None
                        ),
                    ),
                )
                inserted += int(cursor.rowcount == 1)
            conn.commit()

        key = (symbol, horizon, int(due_at_ms))
        with self._path_lock:
            self._paths.setdefault(
                key,
                _PathState(
                    entry=entry,
                    high=entry,
                    low=entry,
                    decision_ms=int(decision_at_ms),
                    tracking_started_ms=int(decision_at_ms),
                ),
            )
        return inserted

    def settle_cycle(
        self,
        *,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        due_at_ms: int,
        open_price_text: str,
        high_price_text: str,
        low_price_text: str,
        close_price_text: str,
        settled_at_ms: int,
        settlement_method: str,
        volume_text: str = "0",
    ) -> int:
        symbol = str(symbol).upper()
        candle_open = _decimal(open_price_text)
        candle_high = _decimal(high_price_text)
        candle_low = _decimal(low_price_text)
        close = _decimal(close_price_text)
        if candle_low > candle_high:
            raise ValueError("candle low cannot exceed candle high")

        # Persist the exact official candle first.  If outcome accounting is
        # interrupted afterward, restart reconciliation can deterministically
        # finish from this immutable evidence instead of inventing a price.
        self.market_state.ingest_closed_candle(
            ClosedCandle(
                symbol=symbol,
                horizon=horizon,
                open_time_ms=int(cycle_start_ms),
                close_time_ms=int(due_at_ms) - 1,
                open_text=str(open_price_text),
                high_text=str(high_price_text),
                low_text=str(low_price_text),
                close_text=str(close_price_text),
                volume_text=str(volume_text),
            ),
            observed_at_ms=int(settled_at_ms),
            source=str(settlement_method),
            evidence_role="PROSPECTIVE",
        )

        key = (symbol, horizon, int(due_at_ms))
        with self._path_lock:
            path = self._paths.pop(key, None)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO quant_market_candles (
                    symbol, horizon_label, cycle_start_ms, due_at_ms,
                    open_text, high_text, low_text, close_text,
                    settled_at_ms, settlement_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, horizon, int(cycle_start_ms), int(due_at_ms),
                    str(open_price_text), str(high_price_text), str(low_price_text),
                    str(close_price_text), int(settled_at_ms), settlement_method,
                ),
            )
            decisions = conn.execute(
                """
                SELECT d.* FROM quant_candidate_decisions d
                WHERE d.symbol = ? AND d.horizon_label = ? AND d.due_at_ms = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM quant_candidate_outcomes o WHERE o.decision_id = d.id
                  )
                ORDER BY d.id
                """,
                (symbol, horizon, int(due_at_ms)),
            ).fetchall()
            inserted = 0
            for decision in decisions:
                entry = _decimal(decision["entry_price_text"])
                if path is not None and path.observations > 0:
                    high = path.high
                    low = path.low
                    exact = path.tracking_started_ms <= int(decision["decision_at_ms"])
                    excursion_source = (
                        "STREAM_AGG_TRADE_AFTER_DECISION"
                        if exact else "STREAM_AGG_TRADE_AFTER_RESTART_PARTIAL"
                    )
                else:
                    high = candle_high
                    low = candle_low
                    excursion_source = "OFFICIAL_KLINE_FULL_CYCLE_FALLBACK"
                outcome = self._outcome_values(decision, entry, high, low, close)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO quant_candidate_outcomes (
                        decision_id, status, settled_at_ms, actual_direction,
                        open_text, high_text, low_text, close_text, mfe_bps,
                        mae_bps, gross_return_bps, cost_bps, net_return_bps,
                        gross_pnl_text, fee_cost_text, slippage_cost_text,
                        net_pnl_text, correct, eligible_trade, excursion_source,
                        settlement_method
                    ) VALUES (?, 'SETTLED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(decision["id"]), int(settled_at_ms), outcome["actual_direction"],
                        str(candle_open), str(high), str(low), str(close),
                        outcome["mfe_bps"], outcome["mae_bps"], outcome["gross_return_bps"],
                        outcome["cost_bps"], outcome["net_return_bps"],
                        str(outcome["gross_pnl"]), str(outcome["fee_cost"]),
                        str(outcome["slippage_cost"]), str(outcome["net_pnl"]),
                        outcome["correct"], outcome["eligible_trade"],
                        excursion_source, settlement_method,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                    self._record_equity(conn, decision, outcome["net_pnl"], int(settled_at_ms))
            conn.commit()
        self.maybe_evaluate(symbol, horizon)
        return inserted

    def reconcile_pending_outcomes(self) -> int:
        """Finish interrupted settlements from immutable official candles."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT d.symbol, d.horizon_label, d.cycle_start_ms,
                       d.due_at_ms, c.open_text, c.high_text, c.low_text,
                       c.close_text, c.volume_text, c.observed_at_ms
                FROM quant_candidate_decisions d
                JOIN quant_market_state_candles c
                  ON c.symbol = d.symbol
                 AND c.horizon_label = d.horizon_label
                 AND c.close_time_ms = d.due_at_ms - 1
                WHERE NOT EXISTS (
                    SELECT 1 FROM quant_candidate_outcomes o
                    WHERE o.decision_id = d.id
                )
                ORDER BY d.due_at_ms
                """
            ).fetchall()
        inserted = 0
        for row in rows:
            inserted += self.settle_cycle(
                symbol=row["symbol"],
                horizon=row["horizon_label"],
                cycle_start_ms=int(row["cycle_start_ms"]),
                due_at_ms=int(row["due_at_ms"]),
                open_price_text=row["open_text"],
                high_price_text=row["high_text"],
                low_price_text=row["low_text"],
                close_price_text=row["close_text"],
                settled_at_ms=int(row["observed_at_ms"]),
                settlement_method="STATE_RECONCILIATION",
                volume_text=row["volume_text"],
            )
        return inserted

    def _outcome_values(
        self,
        decision: sqlite3.Row,
        entry: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
    ) -> Dict[str, Any]:
        actual_direction = "UP" if close > entry else "DOWN" if close < entry else "FLAT"
        action = str(decision["action"])
        eligible = int(
            action in {"UP", "DOWN"}
            and int(decision["primary_benchmark_eligible"] or 0) == 1
        )
        if not eligible:
            return {
                "actual_direction": actual_direction,
                "mfe_bps": None,
                "mae_bps": None,
                "gross_return_bps": 0.0,
                "cost_bps": 0.0,
                "net_return_bps": 0.0,
                "gross_pnl": Decimal("0"),
                "fee_cost": Decimal("0"),
                "slippage_cost": Decimal("0"),
                "net_pnl": Decimal("0"),
                "correct": None,
                "eligible_trade": 0,
            }
        direction = Decimal("1") if action == "UP" else Decimal("-1")
        quantity = STAKE / entry
        gross = quantity * (close - entry) * direction
        exit_notional = quantity * close
        fees = (STAKE + exit_notional) * self.fee_rate
        slippage = (STAKE + exit_notional) * self.slippage_rate
        net = gross - fees - slippage
        if action == "UP":
            mfe = max(Decimal("0"), (high - entry) / entry * Decimal("10000"))
            mae = max(Decimal("0"), (entry - low) / entry * Decimal("10000"))
        else:
            mfe = max(Decimal("0"), (entry - low) / entry * Decimal("10000"))
            mae = max(Decimal("0"), (high - entry) / entry * Decimal("10000"))
        gross_return_bps = gross / STAKE * Decimal("10000")
        cost_bps = (fees + slippage) / STAKE * Decimal("10000")
        net_return_bps = net / STAKE * Decimal("10000")
        return {
            "actual_direction": actual_direction,
            "mfe_bps": float(mfe),
            "mae_bps": float(mae),
            "gross_return_bps": float(gross_return_bps),
            "cost_bps": float(cost_bps),
            "net_return_bps": float(net_return_bps),
            "gross_pnl": _money(gross),
            "fee_cost": _money(fees),
            "slippage_cost": _money(slippage),
            "net_pnl": _money(net),
            "correct": int(actual_direction == action),
            "eligible_trade": 1,
        }

    @staticmethod
    def _record_equity(
        conn: sqlite3.Connection,
        decision: sqlite3.Row,
        net_pnl: Decimal,
        timestamp_ms: int,
    ) -> None:
        previous = conn.execute(
            """
            SELECT balance_after_text FROM quant_candidate_equity
            WHERE symbol = ? AND horizon_label = ? AND candidate_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (decision["symbol"], decision["horizon_label"], decision["candidate_name"]),
        ).fetchone()
        before = Decimal(previous[0]) if previous else STARTING_EQUITY
        after = before + net_pnl
        conn.execute(
            """
            INSERT OR IGNORE INTO quant_candidate_equity (
                decision_id, symbol, horizon_label, candidate_name, regime,
                timestamp_ms, balance_before_text, balance_after_text, net_pnl_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(decision["id"]), decision["symbol"], decision["horizon_label"],
                decision["candidate_name"], decision["regime"], int(timestamp_ms),
                str(_money(before)), str(_money(after)), str(_money(net_pnl)),
            ),
        )

    def void_cycle(
        self,
        *,
        symbol: str,
        horizon: str,
        due_at_ms: int,
        voided_at_ms: int,
        reason: str,
    ) -> int:
        key = (str(symbol).upper(), horizon, int(due_at_ms))
        with self._path_lock:
            self._paths.pop(key, None)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            decisions = conn.execute(
                """
                SELECT d.id FROM quant_candidate_decisions d
                WHERE d.symbol = ? AND d.horizon_label = ? AND d.due_at_ms = ?
                  AND NOT EXISTS (SELECT 1 FROM quant_candidate_outcomes o WHERE o.decision_id = d.id)
                """,
                key,
            ).fetchall()
            for row in decisions:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quant_candidate_outcomes (
                        decision_id, status, settled_at_ms, eligible_trade, void_reason
                    ) VALUES (?, 'VOID', ?, 0, ?)
                    """,
                    (int(row["id"]), int(voided_at_ms), str(reason)),
                )
            conn.commit()
        return len(decisions)

    def maybe_evaluate(self, symbol: str, horizon: str) -> None:
        with self._connect() as conn:
            count = conn.execute(
                """
                SELECT COUNT(DISTINCT d.due_at_ms)
                FROM quant_candidate_decisions d
                JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                WHERE d.symbol = ? AND d.horizon_label = ? AND o.status = 'SETTLED'
                  AND d.evidence_policy_version = ?
                  AND d.primary_benchmark_eligible = 1
                """,
                (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
            ).fetchone()[0]
            latest = conn.execute(
                """
                SELECT report, policy_version FROM quant_tournament_runs
                WHERE symbol = ? AND horizon_label = ?
                ORDER BY id DESC LIMIT 1
                """,
                (symbol, horizon),
            ).fetchone()
        last_evaluated_count = 0
        if latest and latest["policy_version"] == TOURNAMENT_VERSION:
            latest_report = json.loads(latest[0])
            last_evaluated_count = int(latest_report.get("settled_cycles", 0))
        if count <= 0 or count - last_evaluated_count < EVALUATE_EVERY_CYCLES:
            return
        self.evaluate(symbol, horizon)

    def evaluate(self, symbol: str, horizon: str) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "symbol": symbol,
            "horizon": horizon,
            "evaluated_at_ms": int(self._clock_ms()),
            "policy": self.policy(),
            "settled_cycles": 0,
            "regimes": {},
        }
        with self._connect() as conn:
            report["settled_cycles"] = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT d.due_at_ms)
                    FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ? AND o.status = 'SETTLED'
                      AND d.evidence_policy_version = ?
                      AND d.primary_benchmark_eligible = 1
                    """,
                    (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
                ).fetchone()[0]
            )
            regimes = [
                row[0] for row in conn.execute(
                    """
                    SELECT DISTINCT regime FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                      AND evidence_policy_version = ?
                      AND primary_benchmark_eligible = 1
                    ORDER BY regime
                    """,
                    (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
                ).fetchall()
            ]
            for regime in regimes:
                candidates = []
                for spec in CANDIDATES:
                    rows = conn.execute(
                        """
                        SELECT d.*, o.* FROM quant_candidate_decisions d
                        LEFT JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                        WHERE d.symbol = ? AND d.horizon_label = ?
                          AND d.regime = ? AND d.candidate_name = ?
                        ORDER BY d.due_at_ms
                        """,
                        (symbol, horizon, regime, spec.name),
                    ).fetchall()
                    if rows:
                        candidates.append(self._metrics(spec.name, rows))
                self._score_candidates(candidates)
                qualified = [item for item in candidates if item["qualified"]]
                qualified.sort(key=lambda item: item["composite_score"], reverse=True)
                report["regimes"][regime] = {
                    "best_candidate": qualified[0]["candidate"] if qualified else None,
                    "best_score": qualified[0]["composite_score"] if qualified else None,
                    "qualified_count": len(qualified),
                    "candidates": sorted(candidates, key=lambda item: item["composite_score"], reverse=True),
                }

            conn.execute("BEGIN IMMEDIATE")
            for regime, regime_report in report["regimes"].items():
                cursor = conn.execute(
                    """
                    INSERT INTO quant_tournament_runs (
                        evaluated_at_ms, symbol, horizon_label, regime,
                        best_candidate, best_score, qualified_count, report,
                        policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report["evaluated_at_ms"], symbol, horizon, regime,
                        regime_report["best_candidate"], regime_report["best_score"],
                        regime_report["qualified_count"], _json(report), TOURNAMENT_VERSION,
                    ),
                )
                self._apply_promotion_policy(
                    conn, int(cursor.lastrowid), symbol, horizon, regime, regime_report
                )
            conn.commit()
        return report

    @staticmethod
    def _metrics(candidate: str, rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
        all_settled = [row for row in rows if row["status"] == "SETTLED"]
        settled = [
            row
            for row in all_settled
            if row["evidence_policy_version"] == ECONOMIC_EVIDENCE_VERSION
            and int(row["primary_benchmark_eligible"] or 0) == 1
        ]
        voided = [
            row
            for row in rows
            if row["status"] == "VOID"
            and row["evidence_policy_version"] == ECONOMIC_EVIDENCE_VERSION
            and int(row["primary_benchmark_eligible"] or 0) == 1
        ]
        pending = [
            row
            for row in rows
            if row["status"] is None
            and row["evidence_policy_version"] == ECONOMIC_EVIDENCE_VERSION
            and int(row["primary_benchmark_eligible"] or 0) == 1
        ]
        trades = [row for row in settled if int(row["eligible_trade"] or 0) == 1]
        returns = [float(row["net_return_bps"]) for row in trades]
        pnl = [float(row["net_pnl_text"]) for row in trades]
        expectancy_bps = _mean(returns)
        expectancy_usd = _mean(pnl)
        standard_error = _std(pnl) / math.sqrt(len(pnl)) if len(pnl) >= 2 else float("inf")
        ci_low = expectancy_usd - 1.959963984540054 * standard_error if math.isfinite(standard_error) else None
        ci_high = expectancy_usd + 1.959963984540054 * standard_error if math.isfinite(standard_error) else None
        wins = [value for value in pnl if value > 0]
        losses = [value for value in pnl if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for value in pnl:
            cumulative += value
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        max_drawdown_pct = max_drawdown / float(STARTING_EQUITY) * 100.0
        folds = []
        for offset in range(0, len(pnl), FOLD_SIZE):
            fold = pnl[offset: offset + FOLD_SIZE]
            if len(fold) == FOLD_SIZE:
                folds.append(_mean(fold))
        profitable_fold_rate = sum(value > 0 for value in folds) / len(folds) if folds else 0.0
        calibration_values = []
        for row in trades:
            if row["actual_direction"] not in {"UP", "DOWN"}:
                continue
            observed = 1.0 if row["actual_direction"] == "UP" else 0.0
            calibration_values.append((float(row["probability_up"]), observed))
        brier = _mean([(forecast - observed) ** 2 for forecast, observed in calibration_values]) if calibration_values else None
        calibration_error = QuantStrategyTournament._expected_calibration_error(calibration_values)
        reliability = len(settled) / (len(settled) + len(voided)) if settled or voided else 0.0
        participation = len(trades) / len(settled) if settled else 0.0
        trade_sharpe = expectancy_usd / _std(pnl) * math.sqrt(len(pnl)) if len(pnl) >= 2 and _std(pnl) > 0 else None
        gates = {
            "positive_expectancy": expectancy_usd > 0,
            "expectancy_ci_above_zero": ci_low is not None and ci_low > 0,
            "profit_factor_above_one": profit_factor > 1.0,
            "drawdown_within_limit": max_drawdown_pct <= MAX_DRAWDOWN_PCT,
            "sufficient_sample": len(trades) >= MIN_ELIGIBLE_TRADES,
            "walk_forward_folds": len(folds) >= MIN_WALK_FORWARD_FOLDS,
            "walk_forward_stable": profitable_fold_rate >= MIN_PROFITABLE_FOLD_RATE,
            "reliable_settlement": reliability >= MIN_RELIABILITY,
        }
        return {
            "candidate": candidate,
            "cycles": len(settled),
            "pre_policy_preserved_cycles": len(all_settled) - len(settled),
            "eligible_trades": len(trades),
            "no_trade_cycles": len(settled) - len(trades),
            "voided_cycles": len(voided),
            "pending_cycles": len(pending),
            "participation_rate": participation,
            "reliability": reliability,
            "net_expectancy_bps": expectancy_bps,
            "net_expectancy_usd": expectancy_usd,
            "net_pnl_usd": sum(pnl),
            "expectancy_ci_95_low_usd": ci_low,
            "expectancy_ci_95_high_usd": ci_high,
            "profit_factor": profit_factor if math.isfinite(profit_factor) else None,
            "profit_factor_infinite": math.isinf(profit_factor),
            "average_win_usd": _mean(wins),
            "average_loss_usd": _mean(losses),
            "net_win_rate": len(wins) / len(trades) if trades else 0.0,
            "max_drawdown_usd": max_drawdown,
            "max_drawdown_pct": max_drawdown_pct,
            "trade_sharpe": trade_sharpe,
            "walk_forward_folds": len(folds),
            "profitable_fold_rate": profitable_fold_rate,
            "brier_score": brier,
            "calibration_error": calibration_error,
            "average_mfe_bps": _mean([float(row["mfe_bps"]) for row in trades if row["mfe_bps"] is not None]),
            "average_mae_bps": _mean([float(row["mae_bps"]) for row in trades if row["mae_bps"] is not None]),
            "monthly_statistics": QuantStrategyTournament._period_statistics(trades, "month"),
            "quarterly_statistics": QuantStrategyTournament._period_statistics(trades, "quarter"),
            "gates": gates,
            "qualified": all(gates.values()),
            "composite_score": 0.0,
            "score_components": {},
        }

    @staticmethod
    def _period_statistics(
        trades: Sequence[sqlite3.Row], period: str
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[float]] = {}
        for row in trades:
            timestamp = datetime.fromtimestamp(
                int(row["due_at_ms"]) / 1000.0, tz=timezone.utc
            )
            label = (
                f"{timestamp.year:04d}-{timestamp.month:02d}"
                if period == "month"
                else f"{timestamp.year:04d}-Q{(timestamp.month - 1) // 3 + 1}"
            )
            grouped.setdefault(label, []).append(float(row["net_pnl_text"]))
        result = []
        for label in sorted(grouped):
            values = grouped[label]
            wins = [value for value in values if value > 0]
            losses = [value for value in values if value < 0]
            gross_loss = abs(sum(losses))
            result.append(
                {
                    "period": label,
                    "eligible_trades": len(values),
                    "net_pnl_usd": sum(values),
                    "net_expectancy_usd": _mean(values),
                    "net_win_rate": len(wins) / len(values) if values else 0.0,
                    "profit_factor": (
                        sum(wins) / gross_loss if gross_loss > 0 else None
                    ),
                }
            )
        return result

    @staticmethod
    def _expected_calibration_error(values: Sequence[Tuple[float, float]], bins: int = 10) -> Optional[float]:
        if not values:
            return None
        total = len(values)
        error = 0.0
        for index in range(bins):
            low = index / bins
            high = (index + 1) / bins
            selected = [
                item for item in values
                if low <= item[0] < high or (index == bins - 1 and item[0] == 1.0)
            ]
            if selected:
                error += len(selected) / total * abs(_mean([item[0] for item in selected]) - _mean([item[1] for item in selected]))
        return error

    @staticmethod
    def _score_candidates(candidates: List[Dict[str, Any]]) -> None:
        for item in candidates:
            pf = 3.0 if item["profit_factor_infinite"] else float(item["profit_factor"] or 0.0)
            calibration_error = item["calibration_error"]
            components = {
                "expectancy": _clip(item["net_expectancy_bps"] / 10.0, 0.0, 1.0),
                "walk_forward_stability": _clip(item["profitable_fold_rate"], 0.0, 1.0),
                "profit_factor": _clip((pf - 1.0) / 1.0, 0.0, 1.0),
                "drawdown_control": _clip(1.0 - item["max_drawdown_pct"] / MAX_DRAWDOWN_PCT, 0.0, 1.0),
                "calibration": _clip(1.0 - (calibration_error if calibration_error is not None else 1.0) / 0.25, 0.0, 1.0),
                "reliability": _clip(item["reliability"], 0.0, 1.0),
            }
            item["score_components"] = components
            item["composite_score"] = round(
                100.0 * sum(COMPOSITE_WEIGHTS[key] * components[key] for key in COMPOSITE_WEIGHTS),
                4,
            )

    def _apply_promotion_policy(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        symbol: str,
        horizon: str,
        regime: str,
        regime_report: Dict[str, Any],
    ) -> None:
        current = self._current_champion_conn(conn, symbol, horizon, regime)
        current_name = current["champion_name"] if current else None
        metrics_by_name = {item["candidate"]: item for item in regime_report["candidates"]}
        best_name = regime_report["best_candidate"]
        best_metrics = metrics_by_name.get(best_name) if best_name else None
        current_metrics = metrics_by_name.get(current_name) if current_name else None

        if current_name and (current_metrics is None or not current_metrics["qualified"]):
            self._insert_champion_event(
                conn, run_id, symbol, horizon, regime, "DEMOTED", None,
                current_name, 0.0,
                "incumbent no longer clears every mandatory qualification gate",
                current_metrics or {},
            )
            current_name = None
            current_metrics = None

        if best_name is None or best_metrics is None:
            return
        confidence = self._champion_confidence(best_metrics)
        if current_name is None:
            self._insert_champion_event(
                conn, run_id, symbol, horizon, regime, "PROMOTED", best_name,
                None, confidence,
                "candidate cleared every economic and statistical gate",
                best_metrics,
            )
            return
        if best_name == current_name:
            return
        margin = best_metrics["composite_score"] - float(current_metrics["composite_score"])
        if margin < CHALLENGER_MARGIN_POINTS:
            return
        recent = conn.execute(
            """
            SELECT best_candidate, best_score, report FROM quant_tournament_runs
            WHERE symbol = ? AND horizon_label = ? AND regime = ?
            ORDER BY id DESC LIMIT ?
            """,
            (symbol, horizon, regime, CHALLENGER_CONFIRMATIONS),
        ).fetchall()
        if len(recent) < CHALLENGER_CONFIRMATIONS or any(row["best_candidate"] != best_name for row in recent):
            return
        for row in recent:
            historical = json.loads(row["report"])
            historical_candidates = historical.get("regimes", {}).get(regime, {}).get("candidates", [])
            historical_scores = {
                item["candidate"]: float(item["composite_score"])
                for item in historical_candidates
            }
            if (
                best_name not in historical_scores
                or current_name not in historical_scores
                or historical_scores[best_name] - historical_scores[current_name]
                < CHALLENGER_MARGIN_POINTS
            ):
                return
        self._insert_champion_event(
            conn, run_id, symbol, horizon, regime, "REPLACED", best_name,
            current_name, confidence,
            f"challenger led {CHALLENGER_CONFIRMATIONS} consecutive evaluations by at least {CHALLENGER_MARGIN_POINTS:.1f} composite points",
            best_metrics,
        )

    @staticmethod
    def _champion_confidence(metrics: Mapping[str, Any]) -> float:
        ci_low = float(metrics.get("expectancy_ci_95_low_usd") or 0.0)
        expectancy = max(float(metrics.get("net_expectancy_usd") or 0.0), 1e-12)
        ci_strength = _clip(ci_low / expectancy, 0.0, 1.0)
        sample_strength = _clip(float(metrics.get("eligible_trades") or 0) / 500.0, 0.0, 1.0)
        fold_strength = _clip(float(metrics.get("profitable_fold_rate") or 0.0), 0.0, 1.0)
        reliability = _clip(float(metrics.get("reliability") or 0.0), 0.0, 1.0)
        return round(100.0 * (0.40 * ci_strength + 0.25 * sample_strength + 0.25 * fold_strength + 0.10 * reliability), 2)

    @staticmethod
    def _current_champion_conn(
        conn: sqlite3.Connection, symbol: str, horizon: str, regime: str
    ) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT e.* FROM quant_champion_events e
            JOIN quant_tournament_runs r ON r.id = e.tournament_run_id
            WHERE e.symbol = ? AND e.horizon_label = ? AND e.regime = ?
              AND r.policy_version = ?
            ORDER BY e.id DESC LIMIT 1
            """,
            (symbol, horizon, regime, TOURNAMENT_VERSION),
        ).fetchone()

    @staticmethod
    def _insert_champion_event(
        conn: sqlite3.Connection,
        run_id: int,
        symbol: str,
        horizon: str,
        regime: str,
        event_type: str,
        champion_name: Optional[str],
        previous_champion: Optional[str],
        confidence: float,
        reason: str,
        metrics: Mapping[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO quant_champion_events (
                event_at_ms, symbol, horizon_label, regime, event_type,
                champion_name, previous_champion, champion_confidence,
                reason, metrics_snapshot, tournament_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time() * 1000), symbol, horizon, regime, event_type,
                champion_name, previous_champion, confidence, reason,
                _json(dict(metrics)), int(run_id),
            ),
        )

    @staticmethod
    def policy() -> Dict[str, Any]:
        return {
            "version": TOURNAMENT_VERSION,
            "objective": "positive post-cost expectancy",
            "minimum_eligible_trades": MIN_ELIGIBLE_TRADES,
            "walk_forward_fold_size": FOLD_SIZE,
            "minimum_walk_forward_folds": MIN_WALK_FORWARD_FOLDS,
            "minimum_profitable_fold_rate": MIN_PROFITABLE_FOLD_RATE,
            "maximum_drawdown_pct": MAX_DRAWDOWN_PCT,
            "minimum_reliability": MIN_RELIABILITY,
            "challenger_margin_points": CHALLENGER_MARGIN_POINTS,
            "challenger_confirmations": CHALLENGER_CONFIRMATIONS,
            "hard_gates_precede_ranking": True,
            "forced_winner": False,
            "candidate_lifecycle": [
                "COLLECTING", "ELIGIBLE", "QUALIFIED", "CHAMPION", "RETIRED"
            ],
            "active_candidate_count": len(CANDIDATES),
            "market_state_version": MARKET_STATE_VERSION,
            "economic_evidence_version": ECONOMIC_EVIDENCE_VERSION,
            "primary_benchmark": "BOUNDARY_ISSUED_PROSPECTIVE_ONLY",
            "historical_warmup_creates_tournament_evidence": False,
        }


class QuantTournamentBook:
    """Read-only dashboard queries for tournament evidence."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def report(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol).upper()
        empty = {
            "symbol": symbol,
            "label": "SHADOW PAPER TOURNAMENT · REAL BINANCE CYCLES · NOT LIVE CAPITAL",
            "tournament_version": TOURNAMENT_VERSION,
            "policy": QuantStrategyTournament.policy(),
            "horizons": {},
            "history": [],
            "confidentiality": {
                "classification": "CONFIDENTIAL_RESEARCH",
                "exposure": "AGGREGATED_METRICS_ONLY",
                "raw_feature_snapshots_exposed": False,
                "decision_reasons_exposed": False,
                "raw_market_state_features_exposed": False,
                "no_trade_reason_categories_exposed": True,
            },
            "market_state": {
                "version": MARKET_STATE_VERSION,
                "exposure": "AGGREGATED_STATE_ONLY",
                "horizons": {},
            },
            "learning": {
                "tournament_ml": {
                    "source": "ROLLING_FORWARD_ONLY_LOGISTIC_ENSEMBLE",
                    "version": TOURNAMENT_VERSION,
                    "raw_weights_exposed": False,
                    "raw_training_rows_exposed": False,
                    "horizons": {},
                }
            },
        }
        if not os.path.exists(self.db_path):
            return empty
        with self._connect() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quant_candidate_decisions'"
            ).fetchone()
            if table is None:
                return empty
            result = dict(empty)
            state_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quant_market_state_snapshots'"
            ).fetchone()
            if state_table:
                for state_horizon in ("5m", "15m"):
                    state = conn.execute(
                        """
                        SELECT market_state_version, data_state, regime,
                               regime_confidence, candle_count,
                               required_candles, missing_intervals,
                               latest_close_time_ms,
                               latest_close_age_intervals, decision_at_ms
                        FROM quant_market_state_snapshots
                        WHERE symbol = ? AND horizon_label = ?
                        ORDER BY cycle_start_ms DESC LIMIT 1
                        """,
                        (symbol, state_horizon),
                    ).fetchone()
                    if state:
                        result["market_state"]["horizons"][state_horizon] = dict(state)
            for horizon in ("5m", "15m"):
                latest = conn.execute(
                    """
                    SELECT regime, due_at_ms FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                    ORDER BY due_at_ms DESC LIMIT 1
                    """,
                    (symbol, horizon),
                ).fetchone()
                current_regime = latest["regime"] if latest else "unclassified"
                run = conn.execute(
                    """
                    SELECT * FROM quant_tournament_runs
                    WHERE symbol = ? AND horizon_label = ? AND regime = ?
                      AND policy_version = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (symbol, horizon, current_regime, TOURNAMENT_VERSION),
                ).fetchone()
                latest_horizon_run = conn.execute(
                    """
                    SELECT report, policy_version FROM quant_tournament_runs
                    WHERE symbol = ? AND horizon_label = ?
                      AND policy_version = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (symbol, horizon, TOURNAMENT_VERSION),
                ).fetchone()
                champion = conn.execute(
                    """
                    SELECT e.* FROM quant_champion_events e
                    JOIN quant_tournament_runs r ON r.id = e.tournament_run_id
                    WHERE e.symbol = ? AND e.horizon_label = ?
                      AND e.regime = ? AND r.policy_version = ?
                    ORDER BY e.id DESC LIMIT 1
                    """,
                    (symbol, horizon, current_regime, TOURNAMENT_VERSION),
                ).fetchone()
                cycle_rows = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT due_at_ms) FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                decision_records = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                settled_cycles = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT d.due_at_ms)
                    FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ? AND o.status = 'SETTLED'
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                primary_settled_cycles = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT d.due_at_ms)
                    FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ?
                      AND o.status = 'SETTLED'
                      AND d.evidence_policy_version = ?
                      AND d.primary_benchmark_eligible = 1
                    """,
                    (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
                ).fetchone()[0])
                policy_managed_cycles = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT due_at_ms)
                    FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                      AND evidence_policy_version = ?
                    """,
                    (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
                ).fetchone()[0])
                finalized_cycles = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT d.due_at_ms)
                    FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ?
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                settled_decisions = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ? AND o.status = 'SETTLED'
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                voided_decisions = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ? AND o.status = 'VOID'
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                duplicate_groups = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT due_at_ms, candidate_name, COUNT(*) AS copies
                        FROM quant_candidate_decisions
                        WHERE symbol = ? AND horizon_label = ?
                        GROUP BY due_at_ms, candidate_name HAVING copies > 1
                    )
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                outbox_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quant_decision_outbox'"
                ).fetchone()
                pending_outbox = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM quant_decision_outbox
                    WHERE symbol = ? AND horizon_label = ? AND status = 'PENDING'
                    """,
                    (symbol, horizon),
                ).fetchone()[0]) if outbox_table else 0
                state_managed_cycles = int(conn.execute(
                    """
                    SELECT COUNT(DISTINCT due_at_ms)
                    FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                      AND market_state_snapshot_id IS NOT NULL
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                state_managed_decisions = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                      AND market_state_snapshot_id IS NOT NULL
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                broken_state_links = int(conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM quant_candidate_decisions d
                    LEFT JOIN quant_market_state_snapshots s
                      ON s.id = d.market_state_snapshot_id
                    WHERE d.symbol = ? AND d.horizon_label = ?
                      AND d.market_state_snapshot_id IS NOT NULL
                      AND (
                          s.id IS NULL
                          OR s.regime != d.regime
                          OR s.data_state != d.data_state
                          OR s.market_state_version != d.market_state_version
                      )
                    """,
                    (symbol, horizon),
                ).fetchone()[0])
                price_call_columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(price_calls)")
                }
                policy_columns = {
                    "evidence_policy_version",
                    "primary_benchmark_eligible",
                    "timing_class",
                }
                if policy_columns.issubset(price_call_columns):
                    broken_policy_links = int(conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM quant_candidate_decisions d
                        LEFT JOIN price_calls p ON p.id = d.price_call_id
                        WHERE d.symbol = ? AND d.horizon_label = ?
                          AND d.evidence_policy_version = ?
                          AND (
                              p.id IS NULL
                              OR p.evidence_policy_version != d.evidence_policy_version
                              OR COALESCE(p.primary_benchmark_eligible, 0)
                                 != COALESCE(d.primary_benchmark_eligible, 0)
                              OR COALESCE(p.timing_class, '')
                                 != COALESCE(d.timing_class, '')
                          )
                        """,
                        (symbol, horizon, ECONOMIC_EVIDENCE_VERSION),
                    ).fetchone()[0])
                else:
                    broken_policy_links = 0
                last_evaluated_cycles = (
                    int(json.loads(latest_horizon_run["report"]).get("settled_cycles", 0))
                    if latest_horizon_run
                    and latest_horizon_run["policy_version"] == TOURNAMENT_VERSION
                    else 0
                )
                cycles_since_evaluation = max(
                    0, primary_settled_cycles - last_evaluated_cycles
                )
                active_champion = None
                if champion and champion["event_type"] != "DEMOTED" and champion["champion_name"]:
                    survived = int(conn.execute(
                        """
                        SELECT COUNT(*) FROM quant_tournament_runs
                        WHERE symbol = ? AND horizon_label = ? AND regime = ?
                          AND policy_version = ?
                          AND id > ?
                    """,
                        (
                            symbol, horizon, current_regime,
                            TOURNAMENT_VERSION,
                            int(champion["tournament_run_id"]),
                        ),
                    ).fetchone()[0])
                    active_champion = {
                        "name": champion["champion_name"],
                        "confidence": champion["champion_confidence"],
                        "reason": champion["reason"],
                        "event_type": champion["event_type"],
                        "since_ms": champion["event_at_ms"],
                        "metrics": json.loads(champion["metrics_snapshot"]),
                        "challenger_evaluations_survived": survived,
                    }

                live_candidates = []
                for spec in CANDIDATES:
                    rows = conn.execute(
                        """
                        SELECT d.*, o.* FROM quant_candidate_decisions d
                        LEFT JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                        WHERE d.symbol = ? AND d.horizon_label = ?
                          AND d.regime = ? AND d.candidate_name = ?
                        ORDER BY d.due_at_ms
                        """,
                        (symbol, horizon, current_regime, spec.name),
                    ).fetchall()
                    metrics = QuantStrategyTournament._metrics(spec.name, rows)
                    metrics["decisions_recorded"] = len(rows)
                    metrics["candidate_version"] = spec.version
                    latest_candidate_state = conn.execute(
                        """
                        SELECT data_state, market_state_version
                        FROM quant_candidate_decisions
                        WHERE symbol = ? AND horizon_label = ?
                          AND candidate_name = ?
                        ORDER BY due_at_ms DESC LIMIT 1
                        """,
                        (symbol, horizon, spec.name),
                    ).fetchone()
                    main_no_trade = conn.execute(
                        """
                        SELECT no_trade_code, COUNT(*) AS occurrences
                        FROM quant_candidate_decisions
                        WHERE symbol = ? AND horizon_label = ?
                          AND candidate_name = ? AND action = 'NO_TRADE'
                          AND no_trade_code IS NOT NULL
                          AND evidence_policy_version = ?
                        GROUP BY no_trade_code
                        ORDER BY occurrences DESC, no_trade_code LIMIT 1
                        """,
                        (symbol, horizon, spec.name, ECONOMIC_EVIDENCE_VERSION),
                    ).fetchone()
                    metrics["data_ready"] = bool(
                        latest_candidate_state
                        and latest_candidate_state["data_state"] == "READY"
                    )
                    metrics["market_state_version"] = (
                        latest_candidate_state["market_state_version"]
                        if latest_candidate_state else None
                    )
                    metrics["main_no_trade_category"] = (
                        main_no_trade["no_trade_code"] if main_no_trade else None
                    )
                    live_candidates.append(metrics)
                QuantStrategyTournament._score_candidates(live_candidates)

                latest_ml = conn.execute(
                    """
                    SELECT decision_at_ms, due_at_ms, action, no_trade_code,
                           model_fingerprint, model_trained_at_ms,
                           model_training_start_ms, model_training_end_ms,
                           model_training_samples, model_label_up_rate,
                           feature_snapshot
                    FROM quant_candidate_decisions
                    WHERE symbol = ? AND horizon_label = ?
                      AND candidate_name = 'ml_ensemble'
                    ORDER BY due_at_ms DESC LIMIT 1
                    """,
                    (symbol, horizon),
                ).fetchone()
                ml_snapshot: Dict[str, Any] = {}
                if latest_ml:
                    try:
                        ml_snapshot = json.loads(latest_ml["feature_snapshot"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        ml_snapshot = {}
                ml_probability = ml_snapshot.get("ml_probability_up")
                ml_fingerprint = (
                    latest_ml["model_fingerprint"] if latest_ml else None
                )
                ml_telemetry_broken = bool(
                    latest_ml
                    and ml_probability is not None
                    and not ml_fingerprint
                )
                if latest_ml is None:
                    ml_state = "NO_EVIDENCE"
                elif ml_telemetry_broken:
                    ml_state = "DEGRADED"
                elif ml_fingerprint:
                    ml_state = "READY"
                else:
                    ml_state = "WARMING"
                ml_feedback = conn.execute(
                    """
                    SELECT COUNT(*) AS settled_count,
                           MAX(o.settled_at_ms) AS last_settled_at_ms
                    FROM quant_candidate_decisions d
                    JOIN quant_candidate_outcomes o ON o.decision_id = d.id
                    WHERE d.symbol = ? AND d.horizon_label = ?
                      AND d.candidate_name = 'ml_ensemble'
                      AND o.status = 'SETTLED'
                    """,
                    (symbol, horizon),
                ).fetchone()
                importance = ml_snapshot.get("ml_feature_importance") or {}
                safe_importance = [
                    {"name": str(name), "importance": float(value)}
                    for name, value in sorted(
                        importance.items(),
                        key=lambda item: abs(float(item[1])),
                        reverse=True,
                    )
                ]
                result["learning"]["tournament_ml"]["horizons"][horizon] = {
                    "state": ml_state,
                    "decision_state": (
                        latest_ml["no_trade_code"]
                        if latest_ml and latest_ml["action"] == "NO_TRADE"
                        else "SIGNAL_READY" if latest_ml else None
                    ),
                    "model_fingerprint": ml_fingerprint,
                    "trained_at_ms": (
                        latest_ml["model_trained_at_ms"] if latest_ml else None
                    ),
                    "last_inference_at_ms": (
                        latest_ml["decision_at_ms"] if latest_ml else None
                    ),
                    "last_processed_cycle_ms": (
                        latest_ml["due_at_ms"] if latest_ml else None
                    ),
                    "training_window_start_ms": (
                        latest_ml["model_training_start_ms"] if latest_ml else None
                    ),
                    "training_window_end_ms": (
                        latest_ml["model_training_end_ms"] if latest_ml else None
                    ),
                    "training_samples": (
                        int(latest_ml["model_training_samples"] or 0)
                        if latest_ml else 0
                    ),
                    "label_up_rate": (
                        latest_ml["model_label_up_rate"] if latest_ml else None
                    ),
                    "feature_count": len(safe_importance),
                    "feature_importance": safe_importance,
                    "settled_feedback_count": int(ml_feedback["settled_count"] or 0),
                    "last_feedback_at_ms": ml_feedback["last_settled_at_ms"],
                }

                for metrics in live_candidates:
                    if active_champion and metrics["candidate"] == active_champion["name"]:
                        lifecycle = "CHAMPION"
                    elif metrics["qualified"]:
                        lifecycle = "QUALIFIED"
                    elif metrics["gates"]["sufficient_sample"]:
                        lifecycle = "ELIGIBLE"
                    else:
                        lifecycle = "COLLECTING"
                    metrics["lifecycle"] = lifecycle
                    metrics["trades_to_eligibility"] = max(
                        0, MIN_ELIGIBLE_TRADES - metrics["eligible_trades"]
                    )
                    metrics["equity_usd"] = (
                        float(STARTING_EQUITY) + float(metrics["net_pnl_usd"])
                    )

                lifecycle_counts = {
                    state: sum(
                        item["lifecycle"] == state for item in live_candidates
                    )
                    for state in ("COLLECTING", "ELIGIBLE", "QUALIFIED", "CHAMPION", "RETIRED")
                }
                expected_records = cycle_rows * len(CANDIDATES)
                missing_records = max(0, expected_records - decision_records)
                active_cycles = max(0, cycle_rows - finalized_cycles)
                finalized_decisions = settled_decisions + voided_decisions
                expected_finalized_records = finalized_cycles * len(CANDIDATES)
                missing_outcome_records = max(
                    0, expected_finalized_records - finalized_decisions
                )
                expected_state_managed_decisions = (
                    state_managed_cycles * len(CANDIDATES)
                )
                missing_state_decision_links = max(
                    0,
                    expected_state_managed_decisions - state_managed_decisions,
                )
                last_cycle = conn.execute(
                    """
                    SELECT MAX(settled_at_ms) FROM quant_market_candles
                    WHERE symbol = ? AND horizon_label = ?
                    """,
                    (symbol, horizon),
                ).fetchone()[0]
                health_state = (
                    "DEGRADED" if (
                        missing_records or duplicate_groups or missing_outcome_records
                        or missing_state_decision_links or broken_state_links
                        or broken_policy_links or pending_outbox
                        or ml_telemetry_broken
                    )
                    else "INITIALIZING" if cycle_rows == 0
                    else "HEALTHY"
                )
                result["horizons"][horizon] = {
                    "current_regime": current_regime,
                    "cycles_recorded": cycle_rows,
                    "cycles_settled": settled_cycles,
                    "primary_cycles_settled": primary_settled_cycles,
                    "pre_policy_preserved_cycles": max(
                        0, cycle_rows - policy_managed_cycles
                    ),
                    "next_evaluation_in_cycles": max(
                        0, EVALUATE_EVERY_CYCLES - cycles_since_evaluation
                    ),
                    "champion": active_champion,
                    "candidates": live_candidates,
                    "health": {
                        "state": health_state,
                        "active_candidates": len(CANDIDATES),
                        "recorded_cycles": cycle_rows,
                        "settled_cycles": settled_cycles,
                        "finalized_cycles": finalized_cycles,
                        "active_cycles": active_cycles,
                        "expected_decision_records": expected_records,
                        "actual_decision_records": decision_records,
                        "missing_decision_records": missing_records,
                        "duplicate_decision_groups": duplicate_groups,
                        "pending_decision_outbox": pending_outbox,
                        "market_state_managed_cycles": state_managed_cycles,
                        "legacy_pre_market_state_cycles": max(
                            0, cycle_rows - state_managed_cycles
                        ),
                        "market_state_linked_decisions": state_managed_decisions,
                        "expected_market_state_linked_decisions": expected_state_managed_decisions,
                        "missing_market_state_links": missing_state_decision_links,
                        "broken_market_state_links": broken_state_links,
                        "economic_policy_managed_cycles": policy_managed_cycles,
                        "primary_economic_cycles": primary_settled_cycles,
                        "legacy_pre_economic_policy_cycles": max(
                            0, cycle_rows - policy_managed_cycles
                        ),
                        "broken_economic_policy_links": broken_policy_links,
                        "broken_ml_telemetry_links": int(ml_telemetry_broken),
                        "expected_finalized_decision_records": expected_finalized_records,
                        "actual_finalized_decision_records": finalized_decisions,
                        "missing_outcome_records": missing_outcome_records,
                        "settled_candidate_decisions": settled_decisions,
                        "voided_candidate_decisions": voided_decisions,
                        "pending_candidate_decisions": max(
                            0, decision_records - settled_decisions - voided_decisions
                        ),
                        "candidates_with_sufficient_sample": (
                            lifecycle_counts["ELIGIBLE"]
                            + lifecycle_counts["QUALIFIED"]
                            + lifecycle_counts["CHAMPION"]
                        ),
                        "qualified_candidates": (
                            lifecycle_counts["QUALIFIED"] + lifecycle_counts["CHAMPION"]
                        ),
                        "lifecycle_counts": lifecycle_counts,
                        "last_successful_cycle_ms": last_cycle,
                        "last_evaluation_ms": run["evaluated_at_ms"] if run else None,
                    },
                    "last_evaluated_at_ms": run["evaluated_at_ms"] if run else None,
                    "evidence_start_ms": conn.execute(
                        """
                        SELECT MIN(decision_at_ms) FROM quant_candidate_decisions
                        WHERE symbol = ? AND horizon_label = ?
                        """,
                        (symbol, horizon),
                    ).fetchone()[0],
                }
            events = conn.execute(
                """
                SELECT event_at_ms, horizon_label, regime, event_type,
                       champion_name, previous_champion, champion_confidence, reason
                FROM quant_champion_events WHERE symbol = ?
                ORDER BY id DESC LIMIT 30
                """,
                (symbol,),
            ).fetchall()
            result["history"] = [dict(row) for row in events]
            return result


_instances: Dict[str, QuantStrategyTournament] = {}
_instances_lock = threading.Lock()


def get_quant_tournament(
    db_path: str,
    *,
    fee_rate: Decimal = Decimal("0.001"),
    slippage_rate: Decimal = Decimal("0.0002"),
) -> QuantStrategyTournament:
    absolute = os.path.abspath(db_path)
    with _instances_lock:
        instance = _instances.get(absolute)
        if instance is None:
            instance = QuantStrategyTournament(
                absolute, fee_rate=fee_rate, slippage_rate=slippage_rate
            )
            _instances[absolute] = instance
        return instance


def get_quant_book(db_path: str) -> QuantTournamentBook:
    return QuantTournamentBook(os.path.abspath(db_path))
