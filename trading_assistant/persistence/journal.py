"""SQLite-backed decision and trade journal.

This is the system's audit trail: every cycle the orchestrator runs, each
agent's verdict (signal, confidence, risk approval, action taken) is
recorded here so the system's behavior is inspectable after the fact,
not just logged and forgotten.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# High-frequency/operational tables, safe to prune past a retention window -
# every row here has diagnostic value while recent, not long-term audit
# value. Deliberately excludes trades, trade_audit, alerts, retrain_events,
# and circuit_breaker_events: those are the actual evidence-of-performance
# and rare-event audit trail this system exists to produce, kept forever.
_PRUNABLE_TABLES = (
    "decisions",
    "paper_equity_snapshots",
    "system_heartbeat",
    "execution_quality",
    "drift_events",
    "tournament_events",
    "feature_baselines",
    # Cooldown bookkeeping for AlertManager's external-delivery dedup, not
    # an audit trail (every alert OCCURRENCE is already durably recorded
    # in `alerts`, unconditionally, regardless of delivery/dedup outcome -
    # this table only tracks "when did we last actually send channel X
    # for dedupe_key Y", which has zero value once far older than any
    # realistic cooldown_seconds).
    "alert_delivery",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal TEXT,
    confidence REAL,
    risk_approved INTEGER NOT NULL,
    risk_reason TEXT,
    action TEXT,
    price REAL,
    quantity REAL,
    details TEXT
);

-- is_live added via _migrate() below, not here - see that method's
-- docstring for why an existing database needs an explicit ALTER TABLE
-- rather than relying on this CREATE TABLE IF NOT EXISTS. Distinguishes
-- real testnet trades from real LIVE (real money) trades - without this,
-- switching a long-running database from testnet to live mixes both
-- histories into the same "real portfolio" view with no way to tell them
-- apart, producing nonsense figures (a stale testnet trade at a wildly
-- different capital scale showing up as today's live PnL).
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    order_id TEXT,
    pnl REAL
);

-- symbol added via _migrate() below, not here - CREATE TABLE IF NOT EXISTS
-- is a no-op against an already-existing trading_system.db, so a database
-- created before this column existed needs an explicit ALTER TABLE to
-- actually gain it.
CREATE TABLE IF NOT EXISTS retrain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    promoted INTEGER NOT NULL,
    reason TEXT,
    metrics TEXT
);

CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tripped INTEGER NOT NULL,
    reason TEXT
);

-- The engine process (run_system.py) and the dashboard process
-- (webapp/server.py) never share memory - this table is how the engine's
-- own liveness reaches the dashboard, same pattern as every other
-- cross-process metric here (write once, read from a separate process).
-- 'start' is written once per process lifetime, right before the live
-- cycle loop begins (after warm-start); 'cycle' is written once per
-- completed run_cycle(), success or failure, from inside the loop's
-- already-existing per-cycle exception boundary.
CREATE TABLE IF NOT EXISTS system_heartbeat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    cycle_number INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS tournament_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    winners TEXT NOT NULL,
    report TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    candidate TEXT NOT NULL,
    capital REAL NOT NULL,
    total_equity REAL NOT NULL,
    num_trades INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    boundaries TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drift_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    overall_severity TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    context TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT,
    intended_price REAL NOT NULL,
    fill_price REAL NOT NULL,
    slippage_pct REAL,
    latency_ms REAL
);

-- is_live added via _migrate() below - same reasoning as trades.is_live above.
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    capital REAL NOT NULL,
    total_equity REAL NOT NULL,
    positions TEXT NOT NULL
);

-- The immutable trade audit trail: one row per realized (fully or
-- partially closed) trade, written once at close time with both entry and
-- exit context together, so months from now this table alone answers
-- "exactly how did the system perform" without having to reconstruct it
-- from decisions/trades. Records are NEVER updated or deleted (enforced
-- below, not just by convention) - a correction is a new row with
-- corrects_id pointing at the record it supersedes; readers wanting the
-- current view of a trade should take the most recent row in that chain.
CREATE TABLE IF NOT EXISTS trade_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    entry_timestamp TEXT,
    exit_timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    strategy_name TEXT,
    strategy_version TEXT,
    model_confidence REAL,
    market_regime TEXT,
    entry_reason TEXT,
    exit_reason TEXT,
    realized_pnl REAL,
    corrects_id INTEGER REFERENCES trade_audit(id)
);

CREATE TRIGGER IF NOT EXISTS trade_audit_no_update
BEFORE UPDATE ON trade_audit
BEGIN
    SELECT RAISE(ABORT, 'trade_audit is append-only: insert a new row with corrects_id set, never UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS trade_audit_no_delete
BEFORE DELETE ON trade_audit
BEGIN
    SELECT RAISE(ABORT, 'trade_audit is append-only: records are never deleted');
END;

CREATE TABLE IF NOT EXISTS alert_delivery (
    channel TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (channel, dedupe_key)
);
"""


@dataclass
class DecisionRecord:
    id: int
    timestamp: str
    symbol: str
    signal: Optional[str]
    confidence: Optional[float]
    risk_approved: bool
    risk_reason: Optional[str]
    action: Optional[str]
    price: Optional[float]
    quantity: Optional[float]
    details: Dict[str, Any]


class Journal:
    """A small, dependency-free audit log backed by SQLite."""

    def __init__(self, db_path: str = "trading_system.db"):
        self.db_path = db_path
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # WAL mode lets a reader and a writer touch the file concurrently
        # without the whole-file exclusive lock the default rollback-journal
        # mode takes - this is the actual mechanism behind "database is
        # locked" errors under real 24/7 load, since the engine process and
        # this dashboard process both open independent connections to the
        # same file constantly. busy_timeout makes any remaining contention
        # wait-and-retry (up to 5s) instead of raising immediately.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """One-off ALTER TABLE additions for columns introduced after a
        database was already created - _SCHEMA's CREATE TABLE IF NOT EXISTS
        only takes effect on a brand-new database, so an existing
        trading_system.db needs this to actually gain a new column.
        Idempotent (checked via PRAGMA table_info every startup) and cheap
        enough to just always run rather than tracking a schema version."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(retrain_events)").fetchall()}
        if "symbol" not in columns:
            conn.execute("ALTER TABLE retrain_events ADD COLUMN symbol TEXT")

        # is_live distinguishes real testnet trades/equity from real LIVE
        # (real money) ones - every row that predates this column is, by
        # construction, from before this system's first live-mode run
        # (testnet was the only mode that ever existed before today), so
        # backfilling every pre-existing NULL as 0 (testnet) is exactly
        # correct, not a guess. Only pre-existing NULLs are touched (the
        # WHERE clause), so this is safe to run unconditionally on every
        # startup - new rows always explicitly set is_live going forward
        # and are never NULL to begin with.
        for table in ("trades", "equity_snapshots"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "is_live" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN is_live INTEGER")
            conn.execute(f"UPDATE {table} SET is_live = 0 WHERE is_live IS NULL")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_decision(
        self,
        *,
        symbol: str,
        risk_approved: bool,
        signal: Optional[str] = None,
        confidence: Optional[float] = None,
        risk_reason: Optional[str] = None,
        action: Optional[str] = None,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO decisions
                    (timestamp, symbol, signal, confidence, risk_approved, risk_reason, action, price, quantity, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or self._now(),
                    symbol,
                    signal,
                    confidence,
                    1 if risk_approved else 0,
                    risk_reason,
                    action,
                    price,
                    quantity,
                    json.dumps(details or {}),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def record_trade(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        order_id: Optional[str] = None,
        pnl: Optional[float] = None,
        timestamp: Optional[str] = None,
        # None (default) means "unknown" (e.g. a call site that predates
        # this param, or a script that never bothered), stored as SQL NULL
        # rather than silently guessed as testnet - see recent_trades'
        # is_live filter docstring for why leaving this genuinely unknown
        # is safer than a wrong guess.
        is_live: Optional[bool] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (timestamp, symbol, side, price, quantity, order_id, pnl, is_live)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or self._now(), symbol, side, price, quantity, order_id, pnl,
                    None if is_live is None else (1 if is_live else 0),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def record_retrain_event(
        self,
        *,
        promoted: bool,
        symbol: Optional[str] = None,
        reason: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO retrain_events (timestamp, symbol, promoted, reason, metrics)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp or self._now(), symbol, 1 if promoted else 0, reason, json.dumps(metrics or {})),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def record_circuit_breaker_event(
        self, *, tripped: bool, reason: Optional[str] = None, timestamp: Optional[str] = None
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO circuit_breaker_events (timestamp, tripped, reason)
                VALUES (?, ?, ?)
                """,
                (timestamp or self._now(), 1 if tripped else 0, reason),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def latest_circuit_breaker_event(self) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM circuit_breaker_events ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        record = dict(row)
        record["tripped"] = bool(record["tripped"])
        return record

    def record_heartbeat_start(self, *, timestamp: Optional[str] = None) -> int:
        """Written exactly once per engine process lifetime, right before
        the live cycle loop begins - this is what uptime is measured from,
        not process-launch time, so it reflects "actually trading" rather
        than "still warming up"."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO system_heartbeat (timestamp, event, cycle_number, status) VALUES (?, 'start', NULL, NULL)",
                (timestamp or self._now(),),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def record_heartbeat_cycle(
        self, *, cycle_number: int, status: str, timestamp: Optional[str] = None
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO system_heartbeat (timestamp, event, cycle_number, status) VALUES (?, 'cycle', ?, ?)",
                (timestamp or self._now(), cycle_number, status),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def latest_heartbeat_start(self) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM system_heartbeat WHERE event = 'start' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def latest_heartbeat_cycle(self) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM system_heartbeat WHERE event = 'cycle' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def recent_heartbeat_cycles(self, limit: int = 20) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM system_heartbeat WHERE event = 'cycle' ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_tournament_event(
        self, *, symbol: str, winners: Dict[str, Any], report: Dict[str, Any], timestamp: Optional[str] = None
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO tournament_events (timestamp, symbol, winners, report)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp or self._now(), symbol, json.dumps(winners), json.dumps(report)),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def latest_tournament_event(self, symbol: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM tournament_events WHERE symbol = ? ORDER BY id DESC LIMIT 1", (symbol,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["winners"] = json.loads(record["winners"])
        record["report"] = json.loads(record["report"])
        return record

    def record_paper_equity_snapshot(
        self,
        *,
        symbol: str,
        candidate: str,
        capital: float,
        total_equity: float,
        num_trades: int,
        timestamp: Optional[str] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO paper_equity_snapshots (timestamp, symbol, candidate, capital, total_equity, num_trades)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp or self._now(), symbol, candidate, capital, total_equity, num_trades),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_paper_equity_snapshots(
        self, symbol: Optional[str] = None, candidate: Optional[str] = None, limit: int = 2000
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM paper_equity_snapshots"
        clauses = []
        params: List[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if candidate is not None:
            clauses.append("candidate = ?")
            params.append(candidate)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        snapshots = [dict(row) for row in rows]
        snapshots.reverse()
        return snapshots

    def latest_paper_equity_by_candidate(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Most recent snapshot per candidate for a symbol - the leaderboard view."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM paper_equity_snapshots WHERE symbol = ? ORDER BY id ASC", (symbol,)
            ).fetchall()
        latest: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            record = dict(row)
            latest[record["candidate"]] = record
        return latest

    def record_equity_snapshot(
        self,
        *,
        capital: float,
        total_equity: float,
        positions: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
        is_live: Optional[bool] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO equity_snapshots (timestamp, capital, total_equity, positions, is_live)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    timestamp or self._now(), capital, total_equity, json.dumps(positions or {}),
                    None if is_live is None else (1 if is_live else 0),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_equity_snapshots(self, limit: int = 500) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        snapshots = []
        for row in rows:
            record = dict(row)
            record["positions"] = json.loads(record["positions"]) if record["positions"] else {}
            snapshots.append(record)
        snapshots.reverse()  # chronological order for charting
        return snapshots

    def latest_equity_snapshot(self) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        record = dict(row)
        record["positions"] = json.loads(record["positions"]) if record["positions"] else {}
        return record

    def record_feature_baseline(
        self, *, symbol: str, boundaries: Dict[str, List[float]], timestamp: Optional[str] = None
    ) -> int:
        """Persists a compact, equal-frequency-bucket-boundary summary of a
        promoted model's training feature distribution - not the raw
        training rows themselves, which by promotion time aren't worth
        carrying around just to monitor drift later. See
        trading_assistant.ml.drift for how these boundaries are later
        compared against live feature values."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO feature_baselines (timestamp, symbol, boundaries) VALUES (?, ?, ?)",
                (timestamp or self._now(), symbol, json.dumps(boundaries)),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def latest_feature_baseline(self, symbol: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM feature_baselines WHERE symbol = ? ORDER BY id DESC LIMIT 1", (symbol,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["boundaries"] = json.loads(record["boundaries"])
        return record

    def record_drift_event(
        self, *, symbol: str, overall_severity: str, summary: Dict[str, Any], timestamp: Optional[str] = None
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO drift_events (timestamp, symbol, overall_severity, summary) VALUES (?, ?, ?, ?)",
                (timestamp or self._now(), symbol, overall_severity, json.dumps(summary)),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_drift_events(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        query = "SELECT * FROM drift_events"
        params: List[Any] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        events = []
        for row in rows:
            record = dict(row)
            record["summary"] = json.loads(record["summary"])
            events.append(record)
        events.reverse()
        return events

    def record_alert(
        self, *, category: str, severity: str, message: str, context: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO alerts (timestamp, category, severity, message, context) VALUES (?, ?, ?, ?, ?)",
                (timestamp or self._now(), category, severity, message, json.dumps(context or {})),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_alerts(self, limit: int = 100, severity: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM alerts"
        params: List[Any] = []
        if severity is not None:
            query += " WHERE severity = ?"
            params.append(severity)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        alerts = []
        for row in rows:
            record = dict(row)
            record["context"] = json.loads(record["context"]) if record["context"] else {}
            alerts.append(record)
        alerts.reverse()
        return alerts

    def record_alert_delivery(self, *, channel: str, dedupe_key: str, timestamp: Optional[str] = None) -> None:
        """Upserts the last-sent time for (channel, dedupe_key) - backs
        AlertManager's dedup cooldown so it survives a restart. Without
        this, a crash-restart loop (scripts/run_forever.sh retries every
        15s) resets the in-memory cooldown on every attempt, so a
        persistent failure could spam a webhook/email once per restart
        instead of respecting cooldown_seconds across the whole episode."""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO alert_delivery (channel, dedupe_key, timestamp) VALUES (?, ?, ?)
                ON CONFLICT (channel, dedupe_key) DO UPDATE SET timestamp = excluded.timestamp
                """,
                (channel, dedupe_key, timestamp or self._now()),
            )
            conn.commit()

    def last_alert_delivery(self, *, channel: str, dedupe_key: str) -> Optional[str]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT timestamp FROM alert_delivery WHERE channel = ? AND dedupe_key = ?", (channel, dedupe_key)
            ).fetchone()
        return row["timestamp"] if row is not None else None

    def record_execution_quality(
        self,
        *,
        symbol: str,
        side: str,
        intended_price: float,
        fill_price: float,
        order_type: Optional[str] = None,
        slippage_pct: Optional[float] = None,
        latency_ms: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO execution_quality
                    (timestamp, symbol, side, order_type, intended_price, fill_price, slippage_pct, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp or self._now(), symbol, side, order_type, intended_price, fill_price, slippage_pct, latency_ms),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_execution_quality(
        self, symbol: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM execution_quality"
        params: List[Any] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        records = [dict(row) for row in rows]
        records.reverse()
        return records

    def recent_decisions(self, limit: int = 50, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM decisions"
        params: List[Any] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_decision_dict(row) for row in rows]

    def recent_trades(self, limit: int = 50, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM trades"
        params: List[Any] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def record_trade_audit(
        self,
        *,
        symbol: str,
        side: str,
        exit_price: float,
        quantity: float,
        entry_price: Optional[float] = None,
        entry_timestamp: Optional[str] = None,
        strategy_name: Optional[str] = None,
        strategy_version: Optional[str] = None,
        model_confidence: Optional[float] = None,
        market_regime: Optional[str] = None,
        entry_reason: Optional[str] = None,
        exit_reason: Optional[str] = None,
        realized_pnl: Optional[float] = None,
        corrects_id: Optional[int] = None,
        exit_timestamp: Optional[str] = None,
    ) -> int:
        """Append one immutable trade_audit record. Never call this to
        "fix" a mistaken row - call correct_trade_audit instead, which
        inserts a new row referencing the one it supersedes rather than
        mutating anything."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trade_audit (
                    created_at, entry_timestamp, exit_timestamp, symbol, side,
                    entry_price, exit_price, quantity, strategy_name, strategy_version,
                    model_confidence, market_regime, entry_reason, exit_reason,
                    realized_pnl, corrects_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    entry_timestamp,
                    exit_timestamp or self._now(),
                    symbol,
                    side,
                    entry_price,
                    exit_price,
                    quantity,
                    strategy_name,
                    strategy_version,
                    model_confidence,
                    market_regime,
                    entry_reason,
                    exit_reason,
                    realized_pnl,
                    corrects_id,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def recent_trade_audit(self, limit: int = 200, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM trade_audit"
        params: List[Any] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        records = [dict(row) for row in rows]
        records.reverse()
        return records

    def get_trade_audit(self, record_id: int) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM trade_audit WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row is not None else None

    def correct_trade_audit(self, original_id: int, **overrides: Any) -> int:
        """Append a correction: a new row that starts as a copy of
        `original_id`'s fields, with `overrides` applied on top, and
        `corrects_id` pointing back at the record it supersedes. The
        original row is left completely untouched - this is the ONLY
        supported way to fix a trade_audit record after the fact, since
        the table's own triggers reject UPDATE/DELETE outright."""
        original = self.get_trade_audit(original_id)
        if original is None:
            raise ValueError(f"no trade_audit record with id={original_id}")
        fields = {
            "symbol": original["symbol"],
            "side": original["side"],
            "exit_price": original["exit_price"],
            "quantity": original["quantity"],
            "entry_price": original["entry_price"],
            "entry_timestamp": original["entry_timestamp"],
            "strategy_name": original["strategy_name"],
            "strategy_version": original["strategy_version"],
            "model_confidence": original["model_confidence"],
            "market_regime": original["market_regime"],
            "entry_reason": original["entry_reason"],
            "exit_reason": original["exit_reason"],
            "realized_pnl": original["realized_pnl"],
            "exit_timestamp": original["exit_timestamp"],
        }
        fields.update(overrides)
        return self.record_trade_audit(corrects_id=original_id, **fields)

    def prune_old_records(self, retention_days: int) -> Dict[str, int]:
        """Deletes rows older than retention_days from the high-frequency/
        operational tables in _PRUNABLE_TABLES only. trades, trade_audit,
        alerts, retrain_events, and circuit_breaker_events are NEVER
        touched here - see _PRUNABLE_TABLES' own docstring for why.
        Table names are a fixed, hardcoded tuple (never user input), so
        the f-string below is safe the same way _read_events' allowlist
        pattern is in webapp/data_access.py.

        Returns a dict of table -> rows deleted, so a caller can log
        exactly what happened rather than a bare "pruning ran"."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        deleted: Dict[str, int] = {}
        with closing(self._connect()) as conn:
            for table in _PRUNABLE_TABLES:
                cursor = conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                deleted[table] = cursor.rowcount
            conn.commit()
        return deleted

    @staticmethod
    def _row_to_decision_dict(row: sqlite3.Row) -> Dict[str, Any]:
        record = dict(row)
        record["risk_approved"] = bool(record["risk_approved"])
        record["details"] = json.loads(record["details"]) if record["details"] else {}
        return record
