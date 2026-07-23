"""Binance-aligned, self-scoring paper price calls.

Calls use Binance Spot mainnet public data only. They start at an observed
aggregate-trade price, expire on the next exact 5m/15m UTC boundary, and settle
only from Binance's official closed kline. No exchange orders are placed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np

from webapp.economic_evidence import (
    ECONOMIC_EVIDENCE_VERSION,
    assess_economic_action,
    assess_issue_timing,
    build_economic_report,
)

logger = logging.getLogger("BinancePriceCallEngine")

DEFAULT_ANALYTICS_DB = "dashboard_data/analytics.db"
BINANCE_STREAM_BASE = "wss://stream.binance.com:9443/stream"
BINANCE_REST_BASE = "https://api.binance.com"
MARKET_SOURCE = "BINANCE_SPOT_MAINNET"
TIMING_MODE = "EXCHANGE_BOUNDARY_MS"
ENGINE_VERSION = "heuristic-v5-economic-eligibility"

HORIZONS_MS = {"5m": 300_000, "15m": 900_000}
STARTING_BANKROLL = Decimal("50.00")
FIXED_STAKE = Decimal("1.00")
FEE_RATE = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.0002")
MIN_TICKS_FOR_CALL = 10
MISSING_CLOSE_GRACE_MS = 15_000
MIN_EVIDENCE_SAMPLE = 100
MONEY_QUANTUM = Decimal("0.00000001")
STREAM_FRESHNESS_MS = 15_000

PUBLIC_CALL_COLUMNS = """
    id, symbol, horizon_label, direction, price_to_beat, actual_price,
    correct, miss_pct, created_at, due_at, settled_at, voided,
    cycle_start_ms, cycle_close_ms, settlement_latency_ms,
    settlement_method, actual_direction, timing_class,
    evidence_policy_version, economic_action
"""


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("price must be finite and positive")
    return result


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _next_boundary_ms(timestamp_ms: int, interval_ms: int) -> int:
    return ((int(timestamp_ms) // interval_ms) + 1) * interval_ms


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


class MarketMicroState:
    """Thread-safe live evidence cache shared by the stream and API."""

    def __init__(self, max_ticks: int = 200):
        self.max_ticks = max_ticks
        self.tick_buffers: Dict[str, List[dict]] = {}
        self.orderbooks: Dict[str, dict] = {}
        self.latest: Dict[str, dict] = {}
        self.stream: Dict[str, Any] = {
            "connected": False,
            "connected_at_ms": None,
            "last_message_at_ms": None,
            "last_error": None,
        }
        self._lock = threading.Lock()

    def update_trade(
        self, symbol: str, price_text: str, event_ms: int, trade_ms: int
    ) -> None:
        tick = {
            "price": float(_decimal(price_text)),
            "price_text": str(price_text),
            "event_ms": int(event_ms),
            "trade_ms": int(trade_ms),
        }
        with self._lock:
            buffer = self.tick_buffers.setdefault(symbol, [])
            buffer.append(tick)
            if len(buffer) > self.max_ticks:
                del buffer[:-self.max_ticks]
            self.latest[symbol] = dict(tick)
            self.stream["last_message_at_ms"] = int(event_ms)

    def update_orderbook(self, symbol: str, bids: list, asks: list) -> None:
        with self._lock:
            self.orderbooks[symbol] = {"bids": bids, "asks": asks}

    def set_stream_status(self, connected: bool, error: Optional[str] = None) -> None:
        with self._lock:
            self.stream["connected"] = bool(connected)
            if connected:
                self.stream["connected_at_ms"] = int(time.time() * 1000)
                self.stream["last_error"] = None
            elif error:
                self.stream["last_error"] = str(error)[:300]

    def get_state(self, symbol: str) -> Tuple[dict, list]:
        with self._lock:
            depth = self.orderbooks.get(symbol, {"bids": [], "asks": []})
            return dict(depth), list(self.tick_buffers.get(symbol, []))

    def snapshot(self, symbol: str, now_ms: Optional[int] = None) -> dict:
        with self._lock:
            result = dict(self.latest.get(symbol, {}))
            stream = dict(self.stream)
            current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
            last_message_ms = stream.get("last_message_at_ms")
            age_ms = (
                max(0, current_ms - int(last_message_ms))
                if last_message_ms is not None
                else None
            )
            fresh = bool(
                stream.get("connected")
                and age_ms is not None
                and age_ms <= STREAM_FRESHNESS_MS
            )
            stream["age_ms"] = age_ms
            stream["fresh"] = fresh
            stream["status"] = (
                "LIVE" if fresh else "STALE" if stream.get("connected") else "OFFLINE"
            )
            stream["freshness_limit_ms"] = STREAM_FRESHNESS_MS
            result["stream"] = stream
            return result


state_registry = MarketMicroState()


class RealTimePriceCallEngine:
    """Writes aligned calls and their linked paper-bankroll settlements."""

    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB, clock_ms=None):
        self.db_path = db_path
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._attempted_cycles: set[Tuple[str, str, int]] = set()
        self._attempt_lock = threading.Lock()
        self._init_db()
        # The quantitative tournament owns separate additive tables.  Its
        # failures are isolated from this verified price-call ledger.
        from webapp.quant_tournament import get_quant_tournament
        self.quant_tournament = get_quant_tournament(
            self.db_path, fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
        )
        try:
            self._drain_tournament_outbox()
        except Exception:
            logger.exception("Quant tournament decision outbox recovery failed")
        # Complete any outcome batch interrupted after its official candle was
        # durably recorded.  This is idempotent and never synthesizes prices.
        try:
            self.quant_tournament.reconcile_pending_outcomes()
        except Exception:
            logger.exception("Quant tournament restart reconciliation failed")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create the ledger and classify pre-migration timer rows as legacy."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price_to_beat REAL NOT NULL,
                    actual_price REAL,
                    correct INTEGER,
                    miss_pct REAL DEFAULT 0.0,
                    created_at INTEGER NOT NULL,
                    due_at INTEGER NOT NULL,
                    settled_at INTEGER,
                    voided INTEGER NOT NULL DEFAULT 0,
                    prob_up REAL DEFAULT 0.5,
                    prob_down REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.0
                )
                """
            )
            additions = {
                "legacy": "INTEGER NOT NULL DEFAULT 1",
                "engine_version": "TEXT",
                "market_source": "TEXT",
                "timing_mode": "TEXT",
                "evidence_class": "TEXT",
                "entry_price_text": "TEXT",
                "actual_price_text": "TEXT",
                "market_event_ms": "INTEGER",
                "exchange_trade_ms": "INTEGER",
                "cycle_start_ms": "INTEGER",
                "cycle_close_ms": "INTEGER",
                "settlement_event_ms": "INTEGER",
                "settlement_latency_ms": "INTEGER",
                "settlement_method": "TEXT",
                "actual_direction": "TEXT",
                "void_reason": "TEXT",
                "evidence_policy_version": "TEXT",
                "issue_delay_ms": "INTEGER",
                "forecast_lead_ms": "INTEGER",
                "forecast_lead_ratio": "REAL",
                "timing_class": "TEXT",
                "primary_benchmark_eligible": "INTEGER",
                "primary_exclusion_code": "TEXT",
                "expected_move_bps": "REAL",
                "confidence_adjusted_move_bps": "REAL",
                "round_trip_cost_bps": "REAL",
                "cost_hurdle_bps": "REAL",
                "economic_action": "TEXT",
                "economic_decision_code": "TEXT",
                "movement_model_version": "TEXT",
            }
            existing = _columns(conn, "price_calls")
            for name, declaration in additions.items():
                if name not in existing:
                    conn.execute(
                        f'ALTER TABLE price_calls ADD COLUMN "{name}" {declaration}'
                    )
            conn.execute(
                """
                UPDATE price_calls
                SET legacy = 1,
                    evidence_class = COALESCE(
                        evidence_class, 'LEGACY_RELATIVE_TIMER'
                    )
                WHERE market_source IS NULL
                   OR timing_mode IS NULL
                   OR created_at < 1000000000000
                """
            )

            conn.execute("DROP INDEX IF EXISTS uq_price_calls_one_open")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_price_calls_one_open
                ON price_calls (symbol, horizon_label)
                WHERE settled_at IS NULL AND voided = 0 AND legacy = 0
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_price_calls_cycle
                ON price_calls (symbol, horizon_label, due_at)
                WHERE legacy = 0
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_price_calls_evidence
                ON price_calls (
                    symbol, horizon_label, legacy, voided, settled_at
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_price_calls_primary_evidence
                ON price_calls (
                    symbol, horizon_label, evidence_policy_version,
                    primary_benchmark_eligible, settled_at
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_bankroll_accounts (
                    horizon_label TEXT PRIMARY KEY,
                    starting_balance_text TEXT NOT NULL,
                    settled_balance_text TEXT NOT NULL,
                    high_watermark_text TEXT NOT NULL,
                    max_drawdown_text TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_bankroll_positions (
                    call_id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    stake_text TEXT NOT NULL,
                    entry_price_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('OPEN','SETTLED','VOID')),
                    opened_at_ms INTEGER NOT NULL,
                    closed_at_ms INTEGER,
                    FOREIGN KEY (call_id) REFERENCES price_calls(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_bankroll_settlements (
                    call_id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price_text TEXT NOT NULL,
                    exit_price_text TEXT NOT NULL,
                    stake_text TEXT NOT NULL,
                    gross_pnl_text TEXT NOT NULL,
                    fee_cost_text TEXT NOT NULL,
                    slippage_cost_text TEXT NOT NULL,
                    net_pnl_text TEXT NOT NULL,
                    balance_before_text TEXT NOT NULL,
                    balance_after_text TEXT NOT NULL,
                    settled_at_ms INTEGER NOT NULL,
                    engine_version TEXT NOT NULL,
                    FOREIGN KEY (call_id) REFERENCES price_calls(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quant_decision_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    cycle_start_ms INTEGER NOT NULL,
                    due_at_ms INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    entry_price_text TEXT NOT NULL,
                    orderbook_json TEXT,
                    recent_ticks_json TEXT,
                    base_probability_up REAL NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','COMPLETED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_ms INTEGER,
                    last_error_code TEXT,
                    completed_at_ms INTEGER,
                    FOREIGN KEY (call_id) REFERENCES price_calls(id)
                )
                """
            )
            now_ms = int(self._clock_ms())
            for horizon in HORIZONS_MS:
                value = str(STARTING_BANKROLL)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_bankroll_accounts (
                        horizon_label, starting_balance_text,
                        settled_balance_text, high_watermark_text,
                        max_drawdown_text, updated_at_ms
                    ) VALUES (?, ?, ?, ?, '0', ?)
                    """,
                    (horizon, value, value, value, now_ms),
                )
            conn.commit()

    @staticmethod
    def generate_live_features(depth: dict, ticks: list) -> Tuple[float, float, float]:
        if not ticks:
            return 0.5, 0.0, 0.0
        bids = sum(float(row[1]) for row in depth.get("bids", []))
        asks = sum(float(row[1]) for row in depth.get("asks", []))
        pressure = bids / (bids + asks) if bids + asks > 0 else 0.5
        prices = [float(tick["price"]) for tick in ticks]
        if any(not np.isfinite(price) or price <= 0 for price in prices):
            raise ValueError("tick prices must be finite and positive")
        momentum = math.log(prices[-1] / prices[0]) if len(prices) > 1 else 0.0
        volatility = float(np.std(np.diff(prices))) if len(prices) > 2 else 0.0
        return float(pressure), float(momentum), volatility

    def _probabilities(self, depth: dict, ticks: list) -> Tuple[float, float, float]:
        pressure, momentum, _ = self.generate_live_features(depth, ticks)
        bounded = float(np.clip((pressure - 0.5) * 2 + momentum * 100, -50, 50))
        prob_up = float(1 / (1 + np.exp(-bounded)))
        prob_down = 1 - prob_up
        return prob_up, prob_down, abs(prob_up - prob_down)

    def handle_agg_trade(
        self, symbol: str, price_text: str, event_ms: int, trade_ms: int
    ) -> List[int]:
        state_registry.update_trade(symbol, price_text, event_ms, trade_ms)
        self.quant_tournament.observe_trade(symbol, price_text, trade_ms)
        depth, ticks = state_registry.get_state(symbol)
        if len(ticks) < MIN_TICKS_FOR_CALL:
            return []
        due = {
            horizon: _next_boundary_ms(trade_ms, interval)
            for horizon, interval in HORIZONS_MS.items()
        }
        with self._attempt_lock:
            pending = {
                horizon: boundary
                for horizon, boundary in due.items()
                if (symbol, horizon, boundary) not in self._attempted_cycles
            }
            for horizon, boundary in pending.items():
                self._attempted_cycles.add((symbol, horizon, boundary))
        if not pending:
            return []
        try:
            self.void_stale_calls(symbol, trade_ms)
            inserted = self.cast_aligned_calls(
                symbol, price_text, event_ms, trade_ms, depth, ticks, pending.keys()
            )
            recorded = self._recorded_cycles(symbol, pending)
        except Exception:
            recorded = set()
            raise
        finally:
            # An old unresolved call can briefly block the first trade of the
            # new cycle before Binance emits x=true. Unmark that horizon so
            # the next aggregate trade retries after official settlement.
            with self._attempt_lock:
                for horizon, boundary in pending.items():
                    if (horizon, boundary) not in recorded:
                        self._attempted_cycles.discard((symbol, horizon, boundary))
        return inserted

    def _recorded_cycles(
        self, symbol: str, expected: Dict[str, int]
    ) -> set[Tuple[str, int]]:
        if not expected:
            return set()
        placeholders = ",".join("?" for _ in expected)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT horizon_label, due_at FROM price_calls
                WHERE symbol = ? AND legacy = 0
                  AND due_at IN ({placeholders})
                """,
                (symbol, *expected.values()),
            ).fetchall()
        return {(str(row[0]), int(row[1])) for row in rows}

    def cast_aligned_calls(
        self,
        symbol: str,
        price_text: str,
        event_ms: int,
        trade_ms: int,
        orderbook_depth: dict,
        recent_ticks: list,
        horizons: Optional[Iterable[str]] = None,
    ) -> List[int]:
        entry = _decimal(price_text)
        if int(event_ms) <= 0 or int(trade_ms) <= 0:
            raise ValueError("Binance event and trade timestamps must be positive")
        prob_up, prob_down, confidence = self._probabilities(
            orderbook_depth, recent_ticks
        )
        orderbook_json = json.dumps(
            orderbook_depth, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        recent_ticks_json = json.dumps(
            recent_ticks, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        direction = "UP" if prob_up >= prob_down else "DOWN"
        inserted: List[int] = []
        tournament_cycles: List[Tuple[int, str, int]] = []

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            for horizon in list(horizons or HORIZONS_MS):
                interval = HORIZONS_MS[horizon]
                due_at = _next_boundary_ms(trade_ms, interval)
                cycle_start_ms = due_at - interval
                timing = assess_issue_timing(
                    horizon_ms=interval,
                    cycle_start_ms=cycle_start_ms,
                    due_at_ms=due_at,
                    decision_at_ms=int(trade_ms),
                )
                economic = assess_economic_action(
                    direction=direction,
                    confidence=confidence,
                    ticks=recent_ticks,
                    forecast_lead_ms=timing.forecast_lead_ms,
                    decision_at_ms=int(trade_ms),
                    fee_rate=FEE_RATE,
                    slippage_rate=SLIPPAGE_RATE,
                    timing_eligible=timing.primary_eligible,
                )
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO price_calls (
                        symbol, horizon_label, direction, price_to_beat,
                        created_at, due_at, voided, prob_up, prob_down,
                        confidence, legacy, engine_version, market_source,
                        timing_mode, evidence_class, entry_price_text,
                        market_event_ms, exchange_trade_ms, cycle_start_ms,
                        cycle_close_ms, evidence_policy_version,
                        issue_delay_ms, forecast_lead_ms,
                        forecast_lead_ratio, timing_class,
                        primary_benchmark_eligible, primary_exclusion_code,
                        expected_move_bps, confidence_adjusted_move_bps,
                        round_trip_cost_bps, cost_hurdle_bps,
                        economic_action, economic_decision_code,
                        movement_model_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, ?, ?, ?,
                        'VALIDATED_BINANCE_CYCLE', ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        symbol, horizon, direction, float(entry), int(trade_ms),
                        due_at, prob_up, prob_down, confidence, ENGINE_VERSION,
                        MARKET_SOURCE, TIMING_MODE, str(price_text), int(event_ms),
                        int(trade_ms), cycle_start_ms, due_at - 1,
                        timing.policy_version, timing.issue_delay_ms,
                        timing.forecast_lead_ms, timing.forecast_lead_ratio,
                        timing.timing_class, int(timing.primary_eligible),
                        timing.exclusion_code, economic.expected_move_bps,
                        economic.confidence_adjusted_move_bps,
                        economic.round_trip_cost_bps, economic.cost_hurdle_bps,
                        economic.action, economic.decision_code,
                        economic.movement_model_version,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                call_id = int(cursor.lastrowid)
                inserted.append(call_id)
                tournament_cycles.append((call_id, horizon, due_at))
                if economic.action in {"UP", "DOWN"}:
                    self._open_position(
                        conn, call_id, symbol, horizon, economic.action,
                        str(price_text), trade_ms
                    )
                payload_hash = self._outbox_payload_hash(
                    symbol=symbol,
                    horizon=horizon,
                    cycle_start_ms=cycle_start_ms,
                    due_at_ms=due_at,
                    decision_at_ms=int(trade_ms),
                    entry_price_text=str(price_text),
                    orderbook_json=orderbook_json,
                    recent_ticks_json=recent_ticks_json,
                    base_probability_up=prob_up,
                )
                conn.execute(
                    """
                    INSERT INTO quant_decision_outbox (
                        call_id, symbol, horizon_label, cycle_start_ms,
                        due_at_ms, decision_at_ms, entry_price_text,
                        orderbook_json, recent_ticks_json,
                        base_probability_up, payload_hash, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        call_id, symbol, horizon, cycle_start_ms, due_at,
                        int(trade_ms), str(price_text), orderbook_json,
                        recent_ticks_json, prob_up, payload_hash,
                    ),
                )
                logger.info(
                    "[ENGINE PRO CALL CAST] id=%s %s %s %s entry=%s "
                    "issued_ms=%s expiry_ms=%s source=%s economic_action=%s",
                    call_id, symbol, horizon, direction, price_text, trade_ms,
                    due_at, MARKET_SOURCE, economic.action,
                )
            conn.commit()

        self._drain_tournament_outbox([item[0] for item in tournament_cycles])
        return inserted

    @staticmethod
    def _outbox_payload_hash(
        *,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        due_at_ms: int,
        decision_at_ms: int,
        entry_price_text: str,
        orderbook_json: str,
        recent_ticks_json: str,
        base_probability_up: float,
    ) -> str:
        payload = json.dumps(
            {
                "symbol": symbol,
                "horizon": horizon,
                "cycle_start_ms": int(cycle_start_ms),
                "due_at_ms": int(due_at_ms),
                "decision_at_ms": int(decision_at_ms),
                "entry_price_text": str(entry_price_text),
                "orderbook": json.loads(orderbook_json),
                "recent_ticks": json.loads(recent_ticks_json),
                "base_probability_up": float(base_probability_up),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _drain_tournament_outbox(
        self, call_ids: Optional[Iterable[int]] = None
    ) -> int:
        """Idempotently turn durable cycle inputs into candidate decisions."""
        selected = [int(call_id) for call_id in (call_ids or [])]
        where = "WHERE o.status = 'PENDING'"
        params: Tuple[Any, ...] = ()
        if selected:
            placeholders = ",".join("?" for _ in selected)
            where += f" AND o.call_id IN ({placeholders})"
            params = tuple(selected)
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT o.*,
                       p.evidence_policy_version AS call_evidence_policy_version
                FROM quant_decision_outbox o
                JOIN price_calls p ON p.id = o.call_id
                {where}
                ORDER BY o.id
                """,
                params,
            ).fetchall()
        completed = 0
        from webapp.quant_tournament import CANDIDATES

        for row in rows:
            try:
                if row["orderbook_json"] is None or row["recent_ticks_json"] is None:
                    raise ValueError("pending outbox payload is unavailable")
                expected_hash = self._outbox_payload_hash(
                    symbol=row["symbol"],
                    horizon=row["horizon_label"],
                    cycle_start_ms=int(row["cycle_start_ms"]),
                    due_at_ms=int(row["due_at_ms"]),
                    decision_at_ms=int(row["decision_at_ms"]),
                    entry_price_text=row["entry_price_text"],
                    orderbook_json=row["orderbook_json"],
                    recent_ticks_json=row["recent_ticks_json"],
                    base_probability_up=float(row["base_probability_up"]),
                )
                if expected_hash != row["payload_hash"]:
                    raise sqlite3.IntegrityError(
                        "decision outbox payload integrity check failed"
                    )
                self.quant_tournament.record_cycle_decisions(
                    price_call_id=int(row["call_id"]),
                    symbol=row["symbol"],
                    horizon=row["horizon_label"],
                    cycle_start_ms=int(row["cycle_start_ms"]),
                    due_at_ms=int(row["due_at_ms"]),
                    decision_at_ms=int(row["decision_at_ms"]),
                    entry_price_text=row["entry_price_text"],
                    orderbook_depth=json.loads(row["orderbook_json"]),
                    recent_ticks=json.loads(row["recent_ticks_json"]),
                    base_probability_up=float(row["base_probability_up"]),
                    timing_policy_version=(
                        row["call_evidence_policy_version"] or "PRE_POLICY"
                    ),
                )
                with self._connect() as conn:
                    count = int(conn.execute(
                        """
                        SELECT COUNT(*) FROM quant_candidate_decisions
                        WHERE price_call_id = ?
                        """,
                        (int(row["call_id"]),),
                    ).fetchone()[0])
                    if count != len(CANDIDATES):
                        raise sqlite3.IntegrityError(
                            "candidate decision set is incomplete"
                        )
                    now_ms = int(self._clock_ms())
                    conn.execute(
                        """
                        UPDATE quant_decision_outbox
                        SET status = 'COMPLETED', attempts = attempts + 1,
                            last_attempt_ms = ?, last_error_code = NULL,
                            completed_at_ms = ?, orderbook_json = NULL,
                            recent_ticks_json = NULL
                        WHERE id = ? AND status = 'PENDING'
                        """,
                        (now_ms, now_ms, int(row["id"])),
                    )
                completed += 1
            except Exception as exc:
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE quant_decision_outbox
                        SET attempts = attempts + 1, last_attempt_ms = ?,
                            last_error_code = ?
                        WHERE id = ? AND status = 'PENDING'
                        """,
                        (
                            int(self._clock_ms()), type(exc).__name__[:80],
                            int(row["id"]),
                        ),
                    )
                logger.exception(
                    "Quant tournament outbox processing failed for call %s",
                    row["call_id"],
                )
        return completed

    def _open_position(
        self,
        conn: sqlite3.Connection,
        call_id: int,
        symbol: str,
        horizon: str,
        direction: str,
        entry_price_text: str,
        opened_at_ms: int,
    ) -> None:
        account = conn.execute(
            "SELECT settled_balance_text FROM paper_bankroll_accounts WHERE horizon_label = ?",
            (horizon,),
        ).fetchone()
        balance = Decimal(account["settled_balance_text"])
        open_rows = conn.execute(
            "SELECT stake_text FROM paper_bankroll_positions WHERE horizon_label = ? AND status = 'OPEN'",
            (horizon,),
        ).fetchall()
        exposure = sum((Decimal(row["stake_text"]) for row in open_rows), Decimal("0"))
        available = max(Decimal("0"), balance - exposure)
        if available < FIXED_STAKE:
            return
        stake = _money(FIXED_STAKE)
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_bankroll_positions (
                call_id, symbol, horizon_label, direction, stake_text,
                entry_price_text, status, opened_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (call_id, symbol, horizon, direction, str(stake), entry_price_text, int(opened_at_ms)),
        )

    def settle_closed_candle(
        self,
        symbol: str,
        horizon: str,
        cycle_start_ms: int,
        cycle_close_ms: int,
        close_price_text: str,
        event_ms: int,
        settlement_method: str = "STREAM_KLINE",
        *,
        open_price_text: Optional[str] = None,
        high_price_text: Optional[str] = None,
        low_price_text: Optional[str] = None,
        volume_text: str = "0",
    ) -> Optional[int]:
        """Settle exactly once from a Binance kline with ``x=true``."""
        if horizon not in HORIZONS_MS:
            return None
        exit_price = _decimal(close_price_text)
        due_at = int(cycle_close_ms) + 1
        if int(cycle_start_ms) != due_at - HORIZONS_MS[horizon]:
            raise ValueError("closed kline boundaries do not match its horizon")

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            call = conn.execute(
                """
                SELECT * FROM price_calls
                WHERE symbol = ? AND horizon_label = ? AND due_at = ?
                  AND settled_at IS NULL AND voided = 0 AND legacy = 0
                """,
                (symbol, horizon, due_at),
            ).fetchone()
            if call is None:
                conn.commit()
                return None

            entry = _decimal(call["entry_price_text"] or call["price_to_beat"])
            if exit_price > entry:
                actual_direction = "UP"
            elif exit_price < entry:
                actual_direction = "DOWN"
            else:
                actual_direction = "FLAT"
            correct = int(actual_direction == call["direction"])
            move_pct = abs((exit_price - entry) / entry) * Decimal("100")
            latency_ms = max(0, int(event_ms) - due_at)

            updated = conn.execute(
                """
                UPDATE price_calls
                SET actual_price = ?, actual_price_text = ?, correct = ?,
                    miss_pct = ?, settled_at = ?, settlement_event_ms = ?,
                    settlement_latency_ms = ?, actual_direction = ?,
                    settlement_method = ?
                WHERE id = ? AND settled_at IS NULL AND voided = 0
                """,
                (
                    float(exit_price), str(close_price_text), correct,
                    float(move_pct), int(event_ms), int(event_ms), latency_ms,
                    actual_direction, settlement_method, int(call["id"]),
                ),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            self._settle_position(
                conn, int(call["id"]), str(close_price_text), int(event_ms)
            )
            conn.commit()

        try:
            self.quant_tournament.settle_cycle(
                symbol=symbol,
                horizon=horizon,
                cycle_start_ms=int(cycle_start_ms),
                due_at_ms=due_at,
                open_price_text=str(open_price_text or close_price_text),
                high_price_text=str(high_price_text or close_price_text),
                low_price_text=str(low_price_text or close_price_text),
                close_price_text=str(close_price_text),
                settled_at_ms=int(event_ms),
                settlement_method=settlement_method,
                volume_text=str(volume_text),
            )
        except Exception:
            logger.exception(
                "Quant tournament settlement failed without changing price call %s",
                call["id"],
            )

        logger.info(
            "[PRO CALL SETTLED] id=%s %s %s result=%s close=%s "
            "event_ms=%s latency_ms=%s",
            call["id"], symbol, horizon, actual_direction, close_price_text,
            event_ms, latency_ms,
        )
        return int(call["id"])

    def _settle_position(
        self,
        conn: sqlite3.Connection,
        call_id: int,
        exit_price_text: str,
        settled_at_ms: int,
    ) -> None:
        position = conn.execute(
            "SELECT * FROM paper_bankroll_positions WHERE call_id = ? AND status = 'OPEN'",
            (call_id,),
        ).fetchone()
        if position is None:
            return
        account = conn.execute(
            "SELECT * FROM paper_bankroll_accounts WHERE horizon_label = ?",
            (position["horizon_label"],),
        ).fetchone()

        entry = _decimal(position["entry_price_text"])
        exit_price = _decimal(exit_price_text)
        stake = Decimal(position["stake_text"])
        before = Decimal(account["settled_balance_text"])
        quantity = stake / entry
        signed_move = exit_price - entry
        if position["direction"] == "DOWN":
            signed_move = -signed_move
        gross = quantity * signed_move
        exit_notional = quantity * exit_price
        fees = (stake + exit_notional) * FEE_RATE
        slippage = (stake + exit_notional) * SLIPPAGE_RATE
        net = gross - fees - slippage
        after = max(Decimal("0"), before + net)
        high = max(Decimal(account["high_watermark_text"]), after)
        max_drawdown = max(Decimal(account["max_drawdown_text"]), high - after)

        values = {
            "gross": _money(gross), "fees": _money(fees),
            "slippage": _money(slippage), "net": _money(net),
            "before": _money(before), "after": _money(after),
            "high": _money(high), "drawdown": _money(max_drawdown),
        }
        inserted = conn.execute(
            """
            INSERT OR IGNORE INTO paper_bankroll_settlements (
                call_id, symbol, horizon_label, direction,
                entry_price_text, exit_price_text, stake_text,
                gross_pnl_text, fee_cost_text, slippage_cost_text,
                net_pnl_text, balance_before_text, balance_after_text,
                settled_at_ms, engine_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id, position["symbol"], position["horizon_label"],
                position["direction"], position["entry_price_text"],
                exit_price_text, position["stake_text"], str(values["gross"]),
                str(values["fees"]), str(values["slippage"]), str(values["net"]),
                str(values["before"]), str(values["after"]), settled_at_ms,
                ENGINE_VERSION,
            ),
        )
        if inserted.rowcount != 1:
            return
        conn.execute(
            """
            UPDATE paper_bankroll_accounts
            SET settled_balance_text = ?, high_watermark_text = ?,
                max_drawdown_text = ?, updated_at_ms = ?
            WHERE horizon_label = ?
            """,
            (
                str(values["after"]), str(values["high"]),
                str(values["drawdown"]), settled_at_ms,
                position["horizon_label"],
            ),
        )
        conn.execute(
            "UPDATE paper_bankroll_positions SET status = 'SETTLED', closed_at_ms = ? WHERE call_id = ? AND status = 'OPEN'",
            (settled_at_ms, call_id),
        )

    def void_stale_calls(self, symbol: str, as_of_ms: int) -> int:
        """Resume after a missed close without inventing a settlement price."""
        cutoff = int(as_of_ms) - MISSING_CLOSE_GRACE_MS
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, horizon_label, due_at FROM price_calls
                WHERE symbol = ? AND legacy = 0 AND voided = 0
                  AND settled_at IS NULL AND due_at < ?
                """,
                (symbol, cutoff),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            for call_id in ids:
                conn.execute(
                    "UPDATE price_calls SET voided = 1, void_reason = 'MISSING_OFFICIAL_CLOSE' WHERE id = ? AND settled_at IS NULL AND voided = 0",
                    (call_id,),
                )
                conn.execute(
                    "UPDATE paper_bankroll_positions SET status = 'VOID', closed_at_ms = ? WHERE call_id = ? AND status = 'OPEN'",
                    (int(as_of_ms), call_id),
                )
            conn.commit()
        for row in rows:
            try:
                self.quant_tournament.void_cycle(
                    symbol=symbol,
                    horizon=str(row["horizon_label"]),
                    due_at_ms=int(row["due_at"]),
                    voided_at_ms=int(as_of_ms),
                    reason="MISSING_OFFICIAL_CLOSE",
                )
            except Exception:
                logger.exception(
                    "Quant tournament void recording failed for price call %s",
                    row["id"],
                )
        if ids:
            logger.warning("Voided %s stale calls for %s", len(ids), symbol)
        return len(ids)

    def matured_open_calls(self, as_of_ms: int) -> List[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, symbol, horizon_label, cycle_start_ms, due_at
                FROM price_calls
                WHERE legacy = 0 AND voided = 0 AND settled_at IS NULL
                  AND due_at <= ? ORDER BY due_at
                """,
                (int(as_of_ms),),
            ).fetchall()
            return [dict(row) for row in rows]


def _wilson(wins: int, total: int) -> Tuple[Optional[float], Optional[float]]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    p = wins / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _percentile(values: List[int], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(values[lower])
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _calibration_error(rows: list[sqlite3.Row], bins: int = 10) -> Optional[float]:
    rows = [row for row in rows if row["actual_direction"] in ("UP", "DOWN")]
    if not rows:
        return None
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        selected = [
            row for row in rows
            if low <= float(row["prob_up"]) < high
            or (index == bins - 1 and float(row["prob_up"]) == 1.0)
        ]
        if selected:
            forecast = sum(float(row["prob_up"]) for row in selected) / len(selected)
            observed = sum(row["actual_direction"] == "UP" for row in selected) / len(selected)
            error += len(selected) / len(rows) * abs(forecast - observed)
    return error


class PriceCallBook:
    """Read-only queries for validated calls, evidence, and paper results."""

    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB):
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

    def open_call(self, symbol: str) -> List[Dict[str, Any]]:
        if not os.path.exists(self.db_path):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {PUBLIC_CALL_COLUMNS} FROM price_calls
                WHERE symbol = ? AND settled_at IS NULL
                  AND voided = 0 AND legacy = 0
                ORDER BY due_at, horizon_label
                """,
                (symbol,),
            ).fetchall()
            return [dict(row) for row in rows]

    def economic_evidence(self, symbol: str) -> Dict[str, Any]:
        if not os.path.exists(self.db_path):
            return {
                "policy_version": ECONOMIC_EVIDENCE_VERSION,
                "sample_minimum": MIN_EVIDENCE_SAMPLE,
                "primary_definition": "BOUNDARY_ISSUED_PROSPECTIVE_ONLY",
                "horizons": {},
            }
        with self._connect() as conn:
            return build_economic_report(
                conn,
                symbol=symbol,
                horizons=HORIZONS_MS,
                fee_rate=FEE_RATE,
                slippage_rate=SLIPPAGE_RATE,
            )

    def recent_settled(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        if not os.path.exists(self.db_path):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {PUBLIC_CALL_COLUMNS} FROM price_calls
                WHERE symbol = ? AND settled_at IS NOT NULL
                  AND voided = 0 AND legacy = 0
                ORDER BY settled_at DESC LIMIT ?
                """,
                (symbol, max(1, min(int(limit), 200))),
            ).fetchall()
            return [dict(row) for row in rows]

    def stats(self, symbol: str) -> Dict[str, Any]:
        empty = {
            "total_resolved": 0, "win_rate": 0.0, "avg_move_pct": 0.0,
            "by_horizon": {}, "minimum_sample": MIN_EVIDENCE_SAMPLE,
            "scope": "ALL_VALIDATED_OPERATIONAL_HISTORY",
        }
        if not os.path.exists(self.db_path):
            return empty
        by_horizon: Dict[str, dict] = {}
        all_settled: List[sqlite3.Row] = []
        snapshot = state_registry.snapshot(symbol)
        as_of_ms = int(snapshot.get("trade_ms") or time.time() * 1000)

        with self._connect() as conn:
            legacy = conn.execute(
                "SELECT COUNT(*) FROM price_calls WHERE symbol = ? AND legacy = 1",
                (symbol,),
            ).fetchone()[0]
            for horizon, interval in HORIZONS_MS.items():
                rows = conn.execute(
                    "SELECT * FROM price_calls WHERE symbol = ? AND horizon_label = ? AND legacy = 0 ORDER BY due_at",
                    (symbol, horizon),
                ).fetchall()
                settled = [row for row in rows if row["settled_at"] is not None and not row["voided"]]
                directional = [row for row in settled if row["actual_direction"] in ("UP", "DOWN")]
                all_settled.extend(settled)
                wins = sum(int(row["correct"] or 0) for row in directional)
                low, high = _wilson(wins, len(directional))
                brier_values = [
                    (float(row["prob_up"]) - (1.0 if row["actual_direction"] == "UP" else 0.0)) ** 2
                    for row in directional
                ]
                brier = sum(brier_values) / len(brier_values) if brier_values else None
                latencies = sorted(
                    int(row["settlement_latency_ms"])
                    for row in settled if row["settlement_latency_ms"] is not None
                )
                first_due = min((int(row["due_at"]) for row in rows), default=None)
                expected = 0 if first_due is None else max(
                    1, (_next_boundary_ms(as_of_ms, interval) - first_due) // interval + 1
                )
                observed = len(rows)
                by_horizon[horizon] = {
                    "resolved": len(settled),
                    "directional_sample": len(directional),
                    "wins": wins,
                    "hit_rate": wins / len(directional) if directional else 0.0,
                    "wilson_95_low": low,
                    "wilson_95_high": high,
                    "brier_score": brier,
                    "brier_skill": 1 - brier / 0.25 if brier is not None else None,
                    "calibration_error": _calibration_error(settled),
                    "avg_move_pct": (
                        sum(float(row["miss_pct"] or 0) for row in settled) / len(settled)
                        if settled else 0.0
                    ),
                    "sample_status": (
                        "OBSERVED" if len(directional) >= MIN_EVIDENCE_SAMPLE
                        else "INSUFFICIENT_SAMPLE"
                    ),
                    "expected_cycles": expected,
                    "observed_cycles": observed,
                    "missed_cycles": max(0, expected - observed),
                    "voided_cycles": sum(int(row["voided"] or 0) for row in rows),
                    "cycle_coverage": observed / expected if expected else None,
                    "median_settlement_latency_ms": _percentile(latencies, 0.5) if latencies else None,
                    "p95_settlement_latency_ms": _percentile(latencies, 0.95) if latencies else None,
                    "max_settlement_latency_ms": max(latencies) if latencies else None,
                    "rest_recoveries": sum(
                        row["settlement_method"] == "REST_RECOVERY"
                        for row in settled
                    ),
                    "first_issued_ms": min((int(row["created_at"]) for row in rows), default=None),
                    "last_settled_ms": max((int(row["settled_at"]) for row in settled), default=None),
                    "engine_version": ENGINE_VERSION,
                    "market_source": MARKET_SOURCE,
                }

        total = len(all_settled)
        wins = sum(int(row["correct"] or 0) for row in all_settled)
        return {
            "total_resolved": total,
            "win_rate": wins / total if total else 0.0,
            "avg_move_pct": (
                sum(float(row["miss_pct"] or 0) for row in all_settled) / total
                if total else 0.0
            ),
            "by_horizon": by_horizon,
            "legacy_excluded": int(legacy),
            "minimum_sample": MIN_EVIDENCE_SAMPLE,
            "scope": "ALL_VALIDATED_OPERATIONAL_HISTORY",
        }

    def bankroll_report(self, recent_limit: int = 20) -> Dict[str, Any]:
        if not os.path.exists(self.db_path):
            return {}
        report: Dict[str, Any] = {
            "label": "REAL BINANCE MARKET DATA · SIMULATED EXECUTION · NOT LIVE CAPITAL",
            "policy_note": (
                "Append-only ledger: pre-v1.3 exposure is preserved; v1.3 opens "
                "new exposure only after timing and economic eligibility pass."
            ),
            "assumptions": {
                "starting_bankroll": float(STARTING_BANKROLL),
                "fixed_stake_usd": float(FIXED_STAKE),
                "position_limit": "one per symbol/horizon/cycle",
                "fee_rate_per_side_pct": float(FEE_RATE * 100),
                "slippage_per_side_bps": float(SLIPPAGE_RATE * 10_000),
                "pricing": "aggregate-trade entry / official closed-kline exit",
            },
            "horizons": {},
        }
        with self._connect() as conn:
            for horizon in HORIZONS_MS:
                account = conn.execute(
                    "SELECT * FROM paper_bankroll_accounts WHERE horizon_label = ?",
                    (horizon,),
                ).fetchone()
                settlements = conn.execute(
                    "SELECT * FROM paper_bankroll_settlements WHERE horizon_label = ? ORDER BY settled_at_ms",
                    (horizon,),
                ).fetchall()
                positions = conn.execute(
                    "SELECT * FROM paper_bankroll_positions WHERE horizon_label = ? AND status = 'OPEN' ORDER BY opened_at_ms",
                    (horizon,),
                ).fetchall()
                net = [Decimal(row["net_pnl_text"]) for row in settlements]
                wins = [value for value in net if value > 0]
                losses = [value for value in net if value <= 0]
                profit = sum(wins, Decimal("0"))
                loss = abs(sum(losses, Decimal("0")))
                balance = Decimal(account["settled_balance_text"])
                starting = Decimal(account["starting_balance_text"])
                report["horizons"][horizon] = {
                    "settled_balance": float(balance),
                    "starting_balance": float(starting),
                    "open_exposure": float(sum((Decimal(row["stake_text"]) for row in positions), Decimal("0"))),
                    "net_pnl": float(balance - starting),
                    "net_return_pct": float((balance - starting) / starting * 100),
                    "settlements": len(settlements),
                    "net_wins": len(wins), "net_losses": len(losses),
                    "net_win_rate": len(wins) / len(settlements) if settlements else 0.0,
                    "net_expectancy": float(sum(net, Decimal("0")) / len(net)) if net else 0.0,
                    "profit_factor": float(profit / loss) if loss > 0 else None,
                    "max_drawdown": float(Decimal(account["max_drawdown_text"])),
                    "max_drawdown_pct": float(Decimal(account["max_drawdown_text"]) / starting * 100),
                    "total_fees": float(sum((Decimal(row["fee_cost_text"]) for row in settlements), Decimal("0"))),
                    "total_slippage": float(sum((Decimal(row["slippage_cost_text"]) for row in settlements), Decimal("0"))),
                    "open_positions": [dict(row) for row in positions],
                    "recent_settlements": [dict(row) for row in settlements[-max(1, min(recent_limit, 100)):][::-1]],
                }
        return report


def get_book() -> PriceCallBook:
    return PriceCallBook()


def _configured_symbols(config: Any) -> List[str]:
    """Return the one market owned by the price-call evidence engine.

    The wider trading system may monitor many markets, but this paper track
    owns one market at a time so each 5m/15m account has exactly one open $1
    position per cycle. ``PRICE_CALL_SYMBOL`` can select a configured market;
    otherwise the first configured symbol is used.
    """
    result: List[str] = []
    for raw in getattr(config, "symbols", []) or []:
        symbol = str(raw).upper().replace("-", "/")
        if "/" not in symbol and symbol.endswith("USDT"):
            symbol = symbol[:-4] + "/USDT"
        if symbol not in result:
            result.append(symbol)

    requested = os.getenv("PRICE_CALL_SYMBOL", "").strip().upper().replace("-", "/")
    if requested and "/" not in requested and requested.endswith("USDT"):
        requested = requested[:-4] + "/USDT"
    if requested:
        if requested not in result:
            logger.warning(
                "PRICE_CALL_SYMBOL=%s is not configured; price-call stream disabled",
                requested,
            )
            return []
        return [requested]
    return result[:1]


def _exchange_symbol(symbol: str) -> str:
    return symbol.replace("/", "").lower()


def _stream_url(symbols: List[str]) -> str:
    streams: List[str] = []
    for symbol in symbols:
        pair = _exchange_symbol(symbol)
        streams.extend(
            (
                f"{pair}@aggTrade",
                f"{pair}@bookTicker",
                f"{pair}@kline_5m",
                f"{pair}@kline_15m",
            )
        )
    return f"{BINANCE_STREAM_BASE}?streams={'/'.join(streams)}"


def _recover_matured_calls(engine: RealTimePriceCallEngine) -> int:
    """Recover an official close from Binance REST after a socket outage."""
    recovered = 0
    for call in engine.matured_open_calls(int(time.time() * 1000)):
        query = urlencode(
            {
                "symbol": _exchange_symbol(call["symbol"]).upper(),
                "interval": call["horizon_label"],
                "startTime": int(call["cycle_start_ms"]),
                "limit": 1,
            }
        )
        try:
            with urlopen(f"{BINANCE_REST_BASE}/api/v3/klines?{query}", timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload:
                continue
            kline = payload[0]
            if int(kline[6]) + 1 != int(call["due_at"]):
                continue
            settled = engine.settle_closed_candle(
                call["symbol"], call["horizon_label"], int(kline[0]),
                int(kline[6]), str(kline[4]), int(time.time() * 1000),
                settlement_method="REST_RECOVERY",
                open_price_text=str(kline[1]),
                high_price_text=str(kline[2]),
                low_price_text=str(kline[3]),
                volume_text=str(kline[5]),
            )
            recovered += int(settled is not None)
        except Exception as exc:  # stream connection remains the priority
            logger.warning("Could not recover price call %s: %s", call["id"], exc)
    return recovered


def _warm_market_state(
    engine: RealTimePriceCallEngine,
    symbols: List[str],
    *,
    limit: int = 121,
) -> int:
    """Warm indicators from closed Binance candles without backfilling trades."""
    from webapp.market_state import ClosedCandle

    observed_at_ms = int(time.time() * 1000)
    inserted = 0
    for symbol in symbols:
        for horizon in HORIZONS_MS:
            query = urlencode(
                {
                    "symbol": _exchange_symbol(symbol).upper(),
                    "interval": horizon,
                    "limit": max(31, min(int(limit), 500)),
                }
            )
            with urlopen(
                f"{BINANCE_REST_BASE}/api/v3/klines?{query}", timeout=8
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            candles = []
            for kline in payload:
                if len(kline) < 7 or int(kline[6]) >= observed_at_ms:
                    continue
                candles.append(
                    ClosedCandle(
                        symbol=symbol,
                        horizon=horizon,
                        open_time_ms=int(kline[0]),
                        close_time_ms=int(kline[6]),
                        open_text=str(kline[1]),
                        high_text=str(kline[2]),
                        low_text=str(kline[3]),
                        close_text=str(kline[4]),
                        volume_text=str(kline[5]),
                    )
                )
            inserted += engine.quant_tournament.market_state.ingest_closed_candles(
                candles,
                observed_at_ms=observed_at_ms,
                source="BINANCE_REST_WARMUP",
                evidence_role="WARMUP",
            )
    return inserted


def _handle_stream_message(
    engine: RealTimePriceCallEngine,
    symbol_map: Dict[str, str],
    message: dict,
) -> None:
    stream = str(message.get("stream", ""))
    data = message.get("data") or {}
    symbol = symbol_map.get(str(data.get("s", "")).upper())
    if not symbol:
        return
    if stream.endswith("@aggTrade") and data.get("e") == "aggTrade":
        engine.handle_agg_trade(
            symbol, str(data["p"]), int(data["E"]), int(data["T"])
        )
    elif stream.endswith("@bookTicker"):
        state_registry.update_orderbook(
            symbol,
            bids=[[str(data["b"]), str(data["B"])]],
            asks=[[str(data["a"]), str(data["A"])]],
        )
    elif "@kline_" in stream and data.get("e") == "kline":
        candle = data["k"]
        if candle.get("x"):
            engine.settle_closed_candle(
                symbol, str(candle["i"]), int(candle["t"]),
                int(candle["T"]), str(candle["c"]), int(data["E"]),
                open_price_text=str(candle.get("o", candle["c"])),
                high_price_text=str(candle.get("h", candle["c"])),
                low_price_text=str(candle.get("l", candle["c"])),
                volume_text=str(candle.get("v", "0")),
            )


async def run_binance_stream(config: Any) -> None:
    """Maintain one reconnecting public Binance stream for all symbols."""
    import websockets

    symbols = _configured_symbols(config)
    if not symbols:
        logger.warning("Binance price-call stream disabled: no configured symbols")
        return
    engine = RealTimePriceCallEngine()
    symbol_map = {_exchange_symbol(symbol).upper(): symbol for symbol in symbols}
    url = _stream_url(symbols)
    attempt = 0
    market_state_warmed = False
    while True:
        try:
            if not market_state_warmed:
                warmed = await asyncio.to_thread(
                    _warm_market_state, engine, symbols
                )
                market_state_warmed = True
                logger.info(
                    "Market-state warm-up verified for %s; %s new closed candles",
                    ", ".join(symbols), warmed,
                )
            await asyncio.to_thread(_recover_matured_calls, engine)
            await asyncio.to_thread(engine._drain_tournament_outbox)
            await asyncio.to_thread(
                engine.quant_tournament.reconcile_pending_outcomes
            )
            async with websockets.connect(
                url, open_timeout=20, ping_interval=20, ping_timeout=20,
                close_timeout=5, max_queue=4096,
            ) as socket:
                attempt = 0
                state_registry.set_stream_status(True)
                logger.info(
                    "Binance price-call stream connected for %s",
                    ", ".join(symbols),
                )
                async for raw in socket:
                    _handle_stream_message(engine, symbol_map, json.loads(raw))
        except asyncio.CancelledError:
            state_registry.set_stream_status(False)
            raise
        except Exception as exc:
            state_registry.set_stream_status(False, str(exc))
            # Re-run the idempotent REST warm-up after every outage so closed
            # candles missed while disconnected become visible before the
            # next point-in-time decision is recorded.
            market_state_warmed = False
            delay = min(2 ** attempt, 30)
            attempt = min(attempt + 1, 6)
            logger.exception(
                "Binance price-call stream disconnected; retrying in %ss", delay
            )
            await asyncio.sleep(delay)
