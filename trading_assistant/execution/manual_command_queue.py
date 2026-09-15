"""Durable cross-process command queue for human-authorized execution."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional


TERMINAL_STATUSES = {
    "EXECUTED",
    "REJECTED",
    "UNCERTAIN",
}

HUMAN_PROVENANCE = {
    "MANUAL",
    "ATS_ASSISTED_MANUAL",
}


class ManualCommandQueue:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)

        parent = Path(self.db_path).parent
        parent.mkdir(parents=True, exist_ok=True)

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
                CREATE TABLE IF NOT EXISTS manual_commands (
                    request_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,

                    account_id TEXT NOT NULL,
                    operator_id TEXT NOT NULL,

                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,

                    quantity REAL NOT NULL,
                    limit_price REAL,

                    provenance TEXT NOT NULL,
                    confirmation_fingerprint TEXT NOT NULL,

                    status TEXT NOT NULL,

                    claimed_at REAL,
                    completed_at REAL
                );

                CREATE INDEX IF NOT EXISTS
                idx_manual_commands_pending
                ON manual_commands(status, created_at);

                CREATE TABLE IF NOT EXISTS manual_command_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    request_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS
                idx_manual_command_events_request
                ON manual_command_events(request_id, id);
                """
            )

    def enqueue(
        self,
        *,
        request_id: str,
        expires_at: float,
        account_id: str,
        operator_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price: Optional[float],
        provenance: str,
        confirmation_fingerprint: str,
    ) -> bool:
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        provenance = provenance.upper()

        if provenance not in HUMAN_PROVENANCE:
            raise ValueError("manual command requires human provenance")

        if side.lower() not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")

        if order_type.lower() not in {"market", "limit"}:
            raise ValueError("order_type must be market or limit")

        now = time.time()

        if expires_at <= now:
            raise ValueError("cannot enqueue expired command")

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.execute(
                """
                INSERT INTO manual_commands (
                    request_id,
                    created_at,
                    expires_at,
                    account_id,
                    operator_id,
                    symbol,
                    side,
                    order_type,
                    quantity,
                    limit_price,
                    provenance,
                    confirmation_fingerprint,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    request_id,
                    now,
                    expires_at,
                    account_id,
                    operator_id,
                    symbol.upper(),
                    side.lower(),
                    order_type.lower(),
                    float(quantity),
                    (
                        float(limit_price)
                        if limit_price is not None
                        else None
                    ),
                    provenance,
                    confirmation_fingerprint,
                ),
            )

            self._event(
                conn,
                request_id,
                "ENQUEUED",
                {},
                now=now,
            )

            conn.commit()
            return True

        except sqlite3.IntegrityError:
            conn.rollback()
            return False

        finally:
            conn.close()

    def claim_next(
        self,
        *,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.time() if now is None else float(now)

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # Expired commands are terminal. They must never become
            # executable merely because an engine was offline.
            expired = conn.execute(
                """
                SELECT request_id
                FROM manual_commands
                WHERE status = 'PENDING'
                  AND expires_at <= ?
                """,
                (now,),
            ).fetchall()

            for row in expired:
                request_id = row["request_id"]

                conn.execute(
                    """
                    UPDATE manual_commands
                    SET status = 'REJECTED',
                        completed_at = ?
                    WHERE request_id = ?
                      AND status = 'PENDING'
                    """,
                    (now, request_id),
                )

                self._event(
                    conn,
                    request_id,
                    "EXPIRED",
                    {},
                    now=now,
                )

            row = conn.execute(
                """
                SELECT *
                FROM manual_commands
                WHERE status = 'PENDING'
                  AND expires_at > ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()

            if row is None:
                conn.commit()
                return None

            request_id = row["request_id"]

            cursor = conn.execute(
                """
                UPDATE manual_commands
                SET status = 'CLAIMED',
                    claimed_at = ?
                WHERE request_id = ?
                  AND status = 'PENDING'
                """,
                (now, request_id),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                return None

            self._event(
                conn,
                request_id,
                "CLAIMED",
                {},
                now=now,
            )

            conn.commit()

            claimed = dict(row)
            claimed["status"] = "CLAIMED"
            claimed["claimed_at"] = now
            return claimed

        finally:
            conn.close()

    def complete(
        self,
        request_id: str,
        *,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        status = status.upper()

        if status not in TERMINAL_STATUSES:
            raise ValueError(
                f"invalid terminal status: {status}"
            )

        now = time.time()
        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            cursor = conn.execute(
                """
                UPDATE manual_commands
                SET status = ?,
                    completed_at = ?
                WHERE request_id = ?
                  AND status = 'CLAIMED'
                """,
                (
                    status,
                    now,
                    request_id,
                ),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                return False

            self._event(
                conn,
                request_id,
                status,
                details or {},
                now=now,
            )

            conn.commit()
            return True

        finally:
            conn.close()

    def recover_stale_claims(
        self,
        *,
        max_claim_age_seconds: float = 30.0,
        now: Optional[float] = None,
    ) -> int:
        """Fail closed after an engine crash.

        A command abandoned in CLAIMED state becomes UNCERTAIN, never
        PENDING. It must be reconciled against the deterministic broker
        client-order ID before any further action.
        """
        if max_claim_age_seconds <= 0:
            raise ValueError(
                "max_claim_age_seconds must be positive"
            )

        now = time.time() if now is None else float(now)
        cutoff = now - max_claim_age_seconds

        conn = self._connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            rows = conn.execute(
                """
                SELECT request_id
                FROM manual_commands
                WHERE status = 'CLAIMED'
                  AND claimed_at IS NOT NULL
                  AND claimed_at <= ?
                """,
                (cutoff,),
            ).fetchall()

            for row in rows:
                request_id = row["request_id"]

                conn.execute(
                    """
                    UPDATE manual_commands
                    SET status = 'UNCERTAIN',
                        completed_at = ?
                    WHERE request_id = ?
                      AND status = 'CLAIMED'
                    """,
                    (
                        now,
                        request_id,
                    ),
                )

                self._event(
                    conn,
                    request_id,
                    "RECOVERED_AS_UNCERTAIN",
                    {
                        "reason":
                            "engine restarted with stale claimed command"
                    },
                    now=now,
                )

            conn.commit()
            return len(rows)

        finally:
            conn.close()

    def get(
        self,
        request_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM manual_commands
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()

        return dict(row) if row else None

    def events(
        self,
        request_id: str,
    ) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM manual_command_events
                WHERE request_id = ?
                ORDER BY id ASC
                """,
                (request_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        request_id: str,
        event_type: str,
        details: Dict[str, Any],
        *,
        now: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO manual_command_events (
                timestamp,
                request_id,
                event_type,
                details
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                now,
                request_id,
                event_type,
                json.dumps(
                    details,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
