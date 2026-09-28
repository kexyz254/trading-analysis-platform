"""Bounded live-account evidence for the signed Nexuss supervisory bridge.

This module opens the existing trading journal read-only. It never exports
positions, order IDs, free-text reasons, or exchange credentials.
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must have a timezone")
    return parsed.astimezone(timezone.utc)


def _amount(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def account_evidence(db_path: str) -> dict:
    """Report at most 200 movements in 72 hours preceding the last live trip.

    The aggregate covers movements of at least 0.1 quote units in the selected
    interval; `truncated` prevents a consumer from treating a partial list as
    complete. Smaller movements are outside this report's scope.
    The largest snapshot in this window is *not* necessarily the risk agent's
    adjusted peak, which can change on an external capital movement.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute(
            "SELECT timestamp FROM circuit_breaker_events WHERE tripped=1 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"incident_at": None, "window_hours": 72, "status": "no_trip"}
        incident = _utc(row[0])
        start = (incident - timedelta(hours=72)).isoformat()
        end = incident.isoformat()
        peak = db.execute(
            "SELECT timestamp, total_equity FROM equity_snapshots "
            "WHERE is_live=1 AND timestamp>=? AND timestamp<=? "
            "ORDER BY total_equity DESC, id DESC LIMIT 1", (start, end)
        ).fetchone()
        if peak is None:
            return {"incident_at": end, "window_hours": 72,
                    "status": "missing_live_equity"}
        flow_start = peak[0]
        # Ignore zero/noise-size records in the item list and aggregate;
        # they do not explain meaningful changes on this account.
        predicate = ("is_live=1 AND timestamp>=? AND timestamp<=? "
                     "AND ABS(unexplained_delta)>=0.1")
        count, unexplained, classified, unclassified = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(unexplained_delta),0), "
            "COALESCE(SUM(CASE WHEN is_external_flow=1 THEN unexplained_delta ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN is_external_flow=0 THEN unexplained_delta ELSE 0 END),0) "
            "FROM capital_flow_audit WHERE " + predicate, (flow_start, end)
        ).fetchone()
        rows = db.execute(
            "SELECT timestamp, raw_delta, trading_delta, unexplained_delta, is_external_flow "
            "FROM capital_flow_audit WHERE " + predicate + " ORDER BY id LIMIT 201",
            (flow_start, end)
        ).fetchall()
        trades, pnl = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(realized_pnl),0) FROM trade_audit "
            "WHERE exit_timestamp>=? AND exit_timestamp<=?", (flow_start, end)
        ).fetchone()
        latest = db.execute(
            "SELECT timestamp, total_equity FROM equity_snapshots WHERE is_live=1 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "status": "recorded",
            "incident_at": end,
            "window_hours": 72,
            "minimum_movement": 0.1,
            "highest_snapshot_at": _utc(flow_start).isoformat(),
            "highest_snapshot_equity": _amount(peak[1]),
            "last_live_equity_at": _utc(latest[0]).isoformat() if latest else None,
            "last_live_equity": _amount(latest[1]) if latest else None,
            "movement_count": count,
            "movement_sum": _amount(unexplained),
            "classified_external_sum": _amount(classified),
            "unclassified_sum": _amount(unclassified),
            "trade_count": trades,
            "audited_realized_pnl": _amount(pnl),
            "truncated": count > 200,
            "movements": [
                {"at": _utc(at).isoformat(), "raw_delta": _amount(raw),
                 "trading_delta": _amount(trading), "unexplained_delta": _amount(gap),
                 "classified_external": bool(external)}
                for at, raw, trading, gap, external in rows[:200]
            ],
        }
