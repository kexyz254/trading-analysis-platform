"""Durable hard limits for the first real-money autonomous canary.

This guard controls NEW ENTRIES only.

It must never prevent:
- protective stop placement
- reconciliation
- position reduction
- emergency exits
- normal SELL exits

Any serious uncertainty disables future entries while allowing existing
positions to be protected and closed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CanaryLimits:
    max_order_notional: float = 10.0
    max_gross_exposure: float = 20.0
    max_open_positions: int = 2
    max_entries: int = 3
    max_cumulative_loss: float = 5.0


@dataclass(frozen=True)
class CanaryDecision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class CanaryState:
    entry_enabled: bool
    started_at: Optional[float]
    expires_at: Optional[float]
    entries: int
    cumulative_realized_pnl: float
    halt_reason: Optional[str]


class CanaryGuard:
    def __init__(
        self,
        db_path: str,
        limits: Optional[CanaryLimits] = None,
    ):
        self.db_path = str(db_path)
        self.limits = limits or CanaryLimits()

        if self.limits.max_order_notional <= 0:
            raise ValueError("max_order_notional must be positive")
        if self.limits.max_gross_exposure <= 0:
            raise ValueError("max_gross_exposure must be positive")
        if self.limits.max_open_positions <= 0:
            raise ValueError("max_open_positions must be positive")
        if self.limits.max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if self.limits.max_cumulative_loss <= 0:
            raise ValueError("max_cumulative_loss must be positive")

        Path(self.db_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS canary_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    entry_enabled INTEGER NOT NULL,
                    started_at REAL,
                    expires_at REAL,
                    entries INTEGER NOT NULL,
                    cumulative_realized_pnl REAL NOT NULL,
                    halt_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS canary_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL
                );

                INSERT OR IGNORE INTO canary_state (
                    id,
                    entry_enabled,
                    started_at,
                    expires_at,
                    entries,
                    cumulative_realized_pnl,
                    halt_reason
                )
                VALUES (1, 0, NULL, NULL, 0, 0.0, 'not armed');
                """
            )

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        event_type: str,
        details: dict,
    ) -> None:
        conn.execute(
            """
            INSERT INTO canary_events (
                timestamp,
                event_type,
                details
            )
            VALUES (?, ?, ?)
            """,
            (
                time.time(),
                event_type,
                json.dumps(
                    details,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    def state(self) -> CanaryState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM canary_state WHERE id = 1"
            ).fetchone()

        return CanaryState(
            entry_enabled=bool(row["entry_enabled"]),
            started_at=row["started_at"],
            expires_at=row["expires_at"],
            entries=int(row["entries"]),
            cumulative_realized_pnl=float(
                row["cumulative_realized_pnl"]
            ),
            halt_reason=row["halt_reason"],
        )

    def arm(
        self,
        *,
        duration_seconds: float,
    ) -> CanaryState:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

        now = time.time()
        expires_at = now + duration_seconds

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            current = conn.execute(
                "SELECT * FROM canary_state WHERE id = 1"
            ).fetchone()

            if bool(current["entry_enabled"]):
                raise RuntimeError(
                    "canary already armed; explicit halt required first"
                )

            conn.execute(
                """
                UPDATE canary_state
                SET entry_enabled = 1,
                    started_at = ?,
                    expires_at = ?,
                    entries = 0,
                    cumulative_realized_pnl = 0.0,
                    halt_reason = NULL
                WHERE id = 1
                """,
                (now, expires_at),
            )

            self._event(
                conn,
                "ARMED",
                {
                    "expires_at": expires_at,
                    "limits": {
                        "max_order_notional":
                            self.limits.max_order_notional,
                        "max_gross_exposure":
                            self.limits.max_gross_exposure,
                        "max_open_positions":
                            self.limits.max_open_positions,
                        "max_entries":
                            self.limits.max_entries,
                        "max_cumulative_loss":
                            self.limits.max_cumulative_loss,
                    },
                },
            )

            conn.commit()

        finally:
            conn.close()

        return self.state()

    def halt(self, reason: str) -> CanaryState:
        if not reason:
            reason = "unspecified"

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                UPDATE canary_state
                SET entry_enabled = 0,
                    halt_reason = ?
                WHERE id = 1
                """,
                (reason,),
            )

            self._event(
                conn,
                "HALTED",
                {"reason": reason},
            )

            conn.commit()

        finally:
            conn.close()

        return self.state()

    def _expire_if_needed(
        self,
        now: Optional[float] = None,
    ) -> CanaryState:
        now = time.time() if now is None else float(now)
        state = self.state()

        if (
            state.entry_enabled
            and state.expires_at is not None
            and now >= state.expires_at
        ):
            return self.halt("canary time window expired")

        return state

    def authorize_entry(
        self,
        *,
        order_notional: float,
        current_gross_exposure: float,
        open_positions: int,
        now: Optional[float] = None,
    ) -> CanaryDecision:
        state = self._expire_if_needed(now)

        if not state.entry_enabled:
            return CanaryDecision(
                False,
                state.halt_reason or "canary entries disabled",
            )

        if order_notional <= 0:
            return CanaryDecision(
                False,
                "non-positive order notional",
            )

        if order_notional > self.limits.max_order_notional:
            return CanaryDecision(
                False,
                "order exceeds canary per-order notional",
            )

        if (
            current_gross_exposure + order_notional
            > self.limits.max_gross_exposure
        ):
            return CanaryDecision(
                False,
                "order exceeds canary gross exposure",
            )

        if open_positions >= self.limits.max_open_positions:
            return CanaryDecision(
                False,
                "canary open-position limit reached",
            )

        if state.entries >= self.limits.max_entries:
            return CanaryDecision(
                False,
                "canary entry limit reached",
            )

        if (
            state.cumulative_realized_pnl
            <= -self.limits.max_cumulative_loss
        ):
            self.halt("canary cumulative loss limit reached")

            return CanaryDecision(
                False,
                "canary cumulative loss limit reached",
            )

        return CanaryDecision(True, "approved")

    def record_entry(
        self,
        *,
        symbol: str,
        notional: float,
    ) -> CanaryState:
        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT * FROM canary_state WHERE id = 1"
            ).fetchone()

            if not bool(row["entry_enabled"]):
                raise RuntimeError(
                    "cannot record entry while canary disabled"
                )

            entries = int(row["entries"]) + 1

            conn.execute(
                """
                UPDATE canary_state
                SET entries = ?
                WHERE id = 1
                """,
                (entries,),
            )

            self._event(
                conn,
                "ENTRY_EXECUTED",
                {
                    "symbol": symbol,
                    "notional": float(notional),
                    "entry_number": entries,
                },
            )

            conn.commit()

        finally:
            conn.close()

        return self.state()

    def record_realized_pnl(
        self,
        pnl: float,
        *,
        symbol: Optional[str] = None,
    ) -> CanaryState:
        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT * FROM canary_state WHERE id = 1"
            ).fetchone()

            cumulative = (
                float(row["cumulative_realized_pnl"])
                + float(pnl)
            )

            conn.execute(
                """
                UPDATE canary_state
                SET cumulative_realized_pnl = ?
                WHERE id = 1
                """,
                (cumulative,),
            )

            self._event(
                conn,
                "REALIZED_PNL",
                {
                    "symbol": symbol,
                    "pnl": float(pnl),
                    "cumulative_realized_pnl": cumulative,
                },
            )

            if cumulative <= -self.limits.max_cumulative_loss:
                conn.execute(
                    """
                    UPDATE canary_state
                    SET entry_enabled = 0,
                        halt_reason =
                            'canary cumulative loss limit reached'
                    WHERE id = 1
                    """
                )

                self._event(
                    conn,
                    "LOSS_LIMIT_HALT",
                    {
                        "cumulative_realized_pnl": cumulative,
                    },
                )

            conn.commit()

        finally:
            conn.close()

        return self.state()

    def record_uncertain_execution(
        self,
        *,
        request_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> CanaryState:
        reason = "uncertain broker execution"

        if symbol:
            reason += f" on {symbol}"

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                UPDATE canary_state
                SET entry_enabled = 0,
                    halt_reason = ?
                WHERE id = 1
                """,
                (reason,),
            )

            self._event(
                conn,
                "UNCERTAIN_EXECUTION_HALT",
                {
                    "request_id": request_id,
                    "symbol": symbol,
                },
            )

            conn.commit()

        finally:
            conn.close()

        return self.state()
