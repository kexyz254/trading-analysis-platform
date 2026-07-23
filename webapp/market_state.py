"""Point-in-time market-state and regime intelligence for ATS research.

Only fully closed Binance candles enter this store.  Historical candles may
warm indicators, but they never create tournament decisions or outcomes.  A
cycle assessment is reproducible from candles that were both closed before the
cycle and observed before the decision timestamp, preventing look-ahead bias.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


MARKET_STATE_VERSION = "market-state-v1.2.0"
HORIZONS_MS = {"5m": 300_000, "15m": 900_000}
MIN_READY_CANDLES = 30
HISTORY_LIMIT = 120
DATA_STATES = ("READY", "WARMING_UP", "DATA_GAP", "STALE", "INVALID")
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


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid market-state decimal: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("market-state prices must be finite and positive")
    return result


def _volume(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid candle volume: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("candle volume must be finite and non-negative")
    return result


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_decimal_text(value: Any) -> str:
    number = Decimal(str(value))
    return format(number.normalize(), "f")


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
    ordered = sorted(float(item) for item in values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class ClosedCandle:
    symbol: str
    horizon: str
    open_time_ms: int
    close_time_ms: int
    open_text: str
    high_text: str
    low_text: str
    close_text: str
    volume_text: str = "0"


@dataclass(frozen=True)
class MarketStateAssessment:
    symbol: str
    horizon: str
    cycle_start_ms: int
    decision_at_ms: int
    data_state: str
    regime: str
    confidence: float
    candle_count: int
    required_candles: int
    missing_intervals: int
    latest_close_time_ms: Optional[int]
    latest_close_age_intervals: Optional[int]
    state_hash: str
    candles: Tuple[Mapping[str, Any], ...]

    @property
    def ready(self) -> bool:
        return self.data_state == "READY"


class MarketStateEngine:
    """Append-only closed-candle store and deterministic cycle classifier."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS quant_market_state_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    open_time_ms INTEGER NOT NULL,
                    close_time_ms INTEGER NOT NULL,
                    open_text TEXT NOT NULL,
                    high_text TEXT NOT NULL,
                    low_text TEXT NOT NULL,
                    close_text TEXT NOT NULL,
                    volume_text TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    evidence_role TEXT NOT NULL CHECK(evidence_role IN ('WARMUP','PROSPECTIVE')),
                    content_hash TEXT NOT NULL,
                    UNIQUE(symbol, horizon_label, close_time_ms)
                );

                CREATE TABLE IF NOT EXISTS quant_market_state_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    cycle_start_ms INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    market_state_version TEXT NOT NULL,
                    data_state TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    regime_confidence REAL NOT NULL,
                    candle_count INTEGER NOT NULL,
                    required_candles INTEGER NOT NULL,
                    missing_intervals INTEGER NOT NULL,
                    latest_close_time_ms INTEGER,
                    latest_close_age_intervals INTEGER,
                    state_hash TEXT NOT NULL,
                    UNIQUE(symbol, horizon_label, cycle_start_ms)
                );

                CREATE INDEX IF NOT EXISTS idx_market_state_candles_point_in_time
                    ON quant_market_state_candles(
                        symbol, horizon_label, close_time_ms, observed_at_ms
                    );
                CREATE INDEX IF NOT EXISTS idx_market_state_snapshots_cycle
                    ON quant_market_state_snapshots(
                        symbol, horizon_label, cycle_start_ms
                    );

                CREATE TRIGGER IF NOT EXISTS market_state_candles_no_update
                BEFORE UPDATE ON quant_market_state_candles
                BEGIN SELECT RAISE(ABORT, 'market-state candles are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS market_state_candles_no_delete
                BEFORE DELETE ON quant_market_state_candles
                BEGIN SELECT RAISE(ABORT, 'market-state candles are permanent'); END;
                CREATE TRIGGER IF NOT EXISTS market_state_snapshots_no_update
                BEFORE UPDATE ON quant_market_state_snapshots
                BEGIN SELECT RAISE(ABORT, 'market-state snapshots are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS market_state_snapshots_no_delete
                BEFORE DELETE ON quant_market_state_snapshots
                BEGIN SELECT RAISE(ABORT, 'market-state snapshots are permanent'); END;
                """
            )

    @staticmethod
    def _validated_payload(candle: ClosedCandle) -> Dict[str, Any]:
        symbol = str(candle.symbol).upper()
        horizon = str(candle.horizon)
        if horizon not in HORIZONS_MS:
            raise ValueError(f"unsupported market-state horizon: {horizon}")
        interval = HORIZONS_MS[horizon]
        open_time = int(candle.open_time_ms)
        close_time = int(candle.close_time_ms)
        if close_time - open_time + 1 != interval:
            raise ValueError("candle boundaries do not match the declared horizon")
        if open_time % interval != 0 or (close_time + 1) % interval != 0:
            raise ValueError("candle is not aligned to an exchange UTC boundary")
        open_price = _decimal(candle.open_text)
        high = _decimal(candle.high_text)
        low = _decimal(candle.low_text)
        close = _decimal(candle.close_text)
        volume = _volume(candle.volume_text)
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ValueError("candle OHLC ordering is invalid")
        return {
            "symbol": symbol,
            "horizon": horizon,
            "open_time_ms": open_time,
            "close_time_ms": close_time,
            "open_text": str(open_price),
            "high_text": str(high),
            "low_text": str(low),
            "close_text": str(close),
            "volume_text": str(volume),
        }

    def ingest_closed_candle(
        self,
        candle: ClosedCandle,
        *,
        observed_at_ms: int,
        source: str,
        evidence_role: str,
    ) -> bool:
        payload = self._validated_payload(candle)
        observed = int(observed_at_ms)
        if observed <= int(payload["close_time_ms"]):
            raise ValueError("a closed candle cannot be observed before its close")
        role = str(evidence_role).upper()
        if role not in {"WARMUP", "PROSPECTIVE"}:
            raise ValueError("unsupported candle evidence role")
        hash_payload = dict(payload)
        for field in (
            "open_text", "high_text", "low_text", "close_text", "volume_text"
        ):
            hash_payload[field] = _normalized_decimal_text(hash_payload[field])
        content_hash = _hash(hash_payload)
        with self._write_lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO quant_market_state_candles (
                    symbol, horizon_label, open_time_ms, close_time_ms,
                    open_text, high_text, low_text, close_text, volume_text,
                    observed_at_ms, source, evidence_role, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["symbol"], payload["horizon"],
                    payload["open_time_ms"], payload["close_time_ms"],
                    payload["open_text"], payload["high_text"],
                    payload["low_text"], payload["close_text"],
                    payload["volume_text"], observed, str(source)[:80], role,
                    content_hash,
                ),
            )
            if cursor.rowcount == 1:
                return True
            existing = conn.execute(
                """
                SELECT content_hash FROM quant_market_state_candles
                WHERE symbol = ? AND horizon_label = ? AND close_time_ms = ?
                """,
                (
                    payload["symbol"], payload["horizon"],
                    payload["close_time_ms"],
                ),
            ).fetchone()
            if existing is None or existing["content_hash"] != content_hash:
                raise sqlite3.IntegrityError(
                    "conflicting immutable candle for the same market cycle"
                )
            return False

    def ingest_closed_candles(
        self,
        candles: Sequence[ClosedCandle],
        *,
        observed_at_ms: int,
        source: str,
        evidence_role: str = "WARMUP",
    ) -> int:
        inserted = 0
        for candle in sorted(candles, key=lambda item: item.close_time_ms):
            inserted += int(
                self.ingest_closed_candle(
                    candle,
                    observed_at_ms=observed_at_ms,
                    source=source,
                    evidence_role=evidence_role,
                )
            )
        return inserted

    def _point_in_time_history(
        self,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        decision_at_ms: int,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT open_time_ms, close_time_ms, open_text, high_text,
                       low_text, close_text, volume_text, observed_at_ms,
                       source, evidence_role, content_hash
                FROM quant_market_state_candles
                WHERE symbol = ? AND horizon_label = ?
                  AND close_time_ms < ? AND observed_at_ms <= ?
                ORDER BY close_time_ms DESC LIMIT ?
                """,
                (
                    str(symbol).upper(), horizon, int(cycle_start_ms),
                    int(decision_at_ms), HISTORY_LIMIT,
                ),
            ).fetchall()
        return [dict(row) for row in rows[::-1]]

    @staticmethod
    def _classify(candles: Sequence[Mapping[str, Any]]) -> Tuple[str, float]:
        closes = [float(row["close_text"]) for row in candles]
        highs = [float(row["high_text"]) for row in candles]
        lows = [float(row["low_text"]) for row in candles]
        fast = _ema(closes[-12:], 8)
        slow = _ema(closes[-35:], 21)
        trend_bps = (fast / slow - 1.0) * 10_000.0 if slow > 0 else 0.0
        true_ranges: List[float] = []
        for index, close in enumerate(closes):
            previous = closes[index - 1] if index else close
            true_ranges.append(
                max(
                    highs[index] - lows[index],
                    abs(highs[index] - previous),
                    abs(lows[index] - previous),
                ) / previous * 10_000.0
            )
        atr = _mean(true_ranges[-14:])
        historical_atr = true_ranges[:-1] or true_ranges
        high_vol = _percentile(historical_atr, 0.80)
        low_vol = _percentile(historical_atr, 0.20)
        prior_high = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
        prior_low = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1])
        breakout_buffer = max(1.0, atr * 0.10)
        up_break_bps = (closes[-1] / prior_high - 1.0) * 10_000.0
        down_break_bps = (prior_low / closes[-1] - 1.0) * 10_000.0
        trend_threshold = max(8.0, atr * 0.45)
        separation = 0.0
        if high_vol > 0 and atr >= high_vol * 1.15:
            regime = "high_volatility"
            separation = atr / max(high_vol, 1e-9) - 1.0
        elif up_break_bps > breakout_buffer:
            regime = "breakout_up"
            separation = up_break_bps / max(breakout_buffer, 1e-9) - 1.0
        elif down_break_bps > breakout_buffer:
            regime = "breakout_down"
            separation = down_break_bps / max(breakout_buffer, 1e-9) - 1.0
        elif trend_bps >= trend_threshold:
            regime = "trending_up"
            separation = trend_bps / trend_threshold - 1.0
        elif trend_bps <= -trend_threshold:
            regime = "trending_down"
            separation = abs(trend_bps) / trend_threshold - 1.0
        elif low_vol > 0 and atr <= low_vol * 0.85:
            regime = "low_volatility"
            separation = low_vol / max(atr, 1e-9) - 1.0
        else:
            regime = "ranging"
            separation = max(0.0, 1.0 - abs(trend_bps) / trend_threshold)
        sample_strength = min(1.0, len(candles) / 60.0)
        confidence = max(0.05, min(0.99, 0.45 * sample_strength + 0.55 * min(1.0, separation)))
        return regime, round(confidence, 6)

    def assess_cycle(
        self,
        *,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        decision_at_ms: int,
    ) -> MarketStateAssessment:
        symbol = str(symbol).upper()
        if horizon not in HORIZONS_MS:
            raise ValueError(f"unsupported market-state horizon: {horizon}")
        interval = HORIZONS_MS[horizon]
        cycle_start = int(cycle_start_ms)
        decision_at = int(decision_at_ms)
        if cycle_start % interval != 0:
            raise ValueError("cycle start is not exchange-boundary aligned")
        if decision_at < cycle_start:
            raise ValueError("decision timestamp precedes its market cycle")
        candles = self._point_in_time_history(
            symbol, horizon, cycle_start, decision_at
        )
        missing = 0
        for previous, current in zip(candles, candles[1:]):
            delta = int(current["open_time_ms"]) - int(previous["open_time_ms"])
            if delta > interval:
                missing += max(0, delta // interval - 1)
            elif delta != interval:
                missing += 1
        latest_close = int(candles[-1]["close_time_ms"]) if candles else None
        age_intervals = (
            max(0, (cycle_start - 1 - latest_close) // interval)
            if latest_close is not None else None
        )
        if len(candles) < MIN_READY_CANDLES:
            data_state = "WARMING_UP"
        elif missing:
            data_state = "DATA_GAP"
        elif latest_close != cycle_start - 1:
            data_state = "STALE"
        else:
            data_state = "READY"
        regime, confidence = (
            self._classify(candles) if data_state == "READY"
            else ("unclassified", 0.0)
        )
        identity = {
            "version": MARKET_STATE_VERSION,
            "symbol": symbol,
            "horizon": horizon,
            "cycle_start_ms": cycle_start,
            "decision_at_ms": decision_at,
            "data_state": data_state,
            "regime": regime,
            "confidence": confidence,
            "candle_count": len(candles),
            "missing_intervals": missing,
            "latest_candle_hash": candles[-1]["content_hash"] if candles else None,
        }
        return MarketStateAssessment(
            symbol=symbol,
            horizon=horizon,
            cycle_start_ms=cycle_start,
            decision_at_ms=decision_at,
            data_state=data_state,
            regime=regime,
            confidence=confidence,
            candle_count=len(candles),
            required_candles=MIN_READY_CANDLES,
            missing_intervals=missing,
            latest_close_time_ms=latest_close,
            latest_close_age_intervals=age_intervals,
            state_hash=_hash(identity),
            candles=tuple(candles),
        )

    def record_assessment(self, assessment: MarketStateAssessment) -> int:
        with self._write_lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO quant_market_state_snapshots (
                    symbol, horizon_label, cycle_start_ms, decision_at_ms,
                    market_state_version, data_state, regime,
                    regime_confidence, candle_count, required_candles,
                    missing_intervals, latest_close_time_ms,
                    latest_close_age_intervals, state_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment.symbol, assessment.horizon,
                    assessment.cycle_start_ms, assessment.decision_at_ms,
                    MARKET_STATE_VERSION, assessment.data_state,
                    assessment.regime, assessment.confidence,
                    assessment.candle_count, assessment.required_candles,
                    assessment.missing_intervals,
                    assessment.latest_close_time_ms,
                    assessment.latest_close_age_intervals,
                    assessment.state_hash,
                ),
            )
            if cursor.rowcount == 1:
                return int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT id, state_hash FROM quant_market_state_snapshots
                WHERE symbol = ? AND horizon_label = ? AND cycle_start_ms = ?
                """,
                (
                    assessment.symbol, assessment.horizon,
                    assessment.cycle_start_ms,
                ),
            ).fetchone()
            if row is None or row["state_hash"] != assessment.state_hash:
                raise sqlite3.IntegrityError(
                    "market-state replay produced a different immutable cycle state"
                )
            return int(row["id"])

    def latest_public_report(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol).upper()
        report: Dict[str, Any] = {
            "version": MARKET_STATE_VERSION,
            "symbol": symbol,
            "classification": "CONFIDENTIAL_RESEARCH",
            "exposure": "AGGREGATED_STATE_ONLY",
            "horizons": {},
        }
        with self._connect() as conn:
            for horizon in HORIZONS_MS:
                latest = conn.execute(
                    """
                    SELECT market_state_version, data_state, regime,
                           regime_confidence, candle_count, required_candles,
                           missing_intervals, latest_close_time_ms,
                           latest_close_age_intervals, decision_at_ms
                    FROM quant_market_state_snapshots
                    WHERE symbol = ? AND horizon_label = ?
                    ORDER BY cycle_start_ms DESC LIMIT 1
                    """,
                    (symbol, horizon),
                ).fetchone()
                counts = conn.execute(
                    """
                    SELECT COUNT(*) AS candles,
                           SUM(evidence_role = 'WARMUP') AS warmup,
                           SUM(evidence_role = 'PROSPECTIVE') AS prospective,
                           COUNT(DISTINCT close_time_ms) AS distinct_cycles
                    FROM quant_market_state_candles
                    WHERE symbol = ? AND horizon_label = ?
                    """,
                    (symbol, horizon),
                ).fetchone()
                payload = dict(latest) if latest else {
                    "market_state_version": MARKET_STATE_VERSION,
                    "data_state": "WARMING_UP",
                    "regime": "unclassified",
                    "regime_confidence": 0.0,
                    "candle_count": int(counts["candles"] or 0),
                    "required_candles": MIN_READY_CANDLES,
                    "missing_intervals": 0,
                    "latest_close_time_ms": None,
                    "latest_close_age_intervals": None,
                    "decision_at_ms": None,
                }
                payload["stored_candles"] = int(counts["candles"] or 0)
                payload["warmup_candles"] = int(counts["warmup"] or 0)
                payload["prospective_candles"] = int(counts["prospective"] or 0)
                payload["duplicate_candle_records"] = max(
                    0,
                    int(counts["candles"] or 0) - int(counts["distinct_cycles"] or 0),
                )
                report["horizons"][horizon] = payload
        return report


_instances: Dict[str, MarketStateEngine] = {}
_instances_lock = threading.Lock()


def get_market_state(db_path: str) -> MarketStateEngine:
    absolute = os.path.abspath(db_path)
    with _instances_lock:
        instance = _instances.get(absolute)
        if instance is None:
            instance = MarketStateEngine(absolute)
            _instances[absolute] = instance
        return instance
