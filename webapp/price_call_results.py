"""Read-only BTC price-call outcome tracker using the existing analytics ledger.

All metrics describe paper price calls, not realized trading returns. The
engine's recorded settlement and eligibility flags remain the source of truth.
"""

from __future__ import annotations

import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path


SYMBOL = "BTC/USDT"
HORIZONS = ("5m", "15m")
MIN_SAMPLE = 100
REQUIRED = frozenset({
    "id", "symbol", "horizon_label", "legacy", "voided", "settled_at", "correct",
    "actual_direction", "created_at", "due_at", "direction", "price_to_beat",
    "actual_price", "settlement_method", "primary_benchmark_eligible",
})


def _finite(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _period(db, horizon, start_ms, now_ms):
    row = db.execute("""
        SELECT
          COUNT(*) AS resolved,
          SUM(CASE WHEN actual_direction IN ('UP','DOWN') THEN 1 ELSE 0 END) AS directional,
          SUM(CASE WHEN actual_direction IN ('UP','DOWN') AND correct=1 THEN 1 ELSE 0 END) AS hits,
          SUM(CASE WHEN actual_direction IN ('UP','DOWN') AND primary_benchmark_eligible=1
                   THEN 1 ELSE 0 END) AS benchmark_eligible,
          MAX(settled_at) AS latest_settled_ms
        FROM price_calls WHERE symbol=? AND horizon_label=? AND legacy=0
          AND voided=0 AND settled_at IS NOT NULL
          AND settled_at>=? AND settled_at<=?
    """, (SYMBOL, horizon, start_ms, now_ms)).fetchone()
    directional = int(row["directional"] or 0)
    hits = int(row["hits"] or 0)
    return {"resolved": int(row["resolved"]), "directional": directional,
            "hits": hits, "hit_rate": hits / directional if directional else None,
            "benchmark_eligible": int(row["benchmark_eligible"] or 0),
            "latest_settled_ms": row["latest_settled_ms"],
            "sample_sufficient": directional >= MIN_SAMPLE}


def results(db_path: str, *, now_ms: int | None = None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    target = Path(db_path)
    if not target.is_file():
        return {"symbol": SYMBOL, "status": "no_analytics_db", "as_of_ms": now_ms,
                "horizons": {}}
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        fields = {row[1] for row in db.execute("PRAGMA table_info(price_calls)")}
        if not REQUIRED <= fields:
            raise ValueError("Price-call schema unavailable")
        out = {}
        for horizon in HORIZONS:
            total = db.execute("""
                SELECT COUNT(*) AS recorded,
                  SUM(CASE WHEN voided=1 THEN 1 ELSE 0 END) AS voided,
                  SUM(CASE WHEN voided=0 AND settled_at IS NULL THEN 1 ELSE 0 END) AS open
                FROM price_calls WHERE symbol=? AND horizon_label=? AND legacy=0
            """, (SYMBOL, horizon)).fetchone()
            recent = db.execute("""
                SELECT created_at, due_at, settled_at, direction, actual_direction,
                  price_to_beat, actual_price, correct, settlement_method,
                  primary_benchmark_eligible
                FROM price_calls WHERE symbol=? AND horizon_label=? AND legacy=0
                  AND voided=0 AND settled_at IS NOT NULL AND settled_at<=?
                ORDER BY settled_at DESC, id DESC LIMIT 10
            """, (SYMBOL, horizon, now_ms)).fetchall()
            out[horizon] = {
                "recorded": int(total["recorded"]),
                "voided": int(total["voided"] or 0),
                "open": int(total["open"] or 0),
                "last_24h": _period(db, horizon, now_ms - 86_400_000, now_ms),
                "last_7d": _period(db, horizon, now_ms - 7 * 86_400_000, now_ms),
                "recent": [{"issued_ms": row["created_at"], "due_ms": row["due_at"],
                            "settled_ms": row["settled_at"], "direction": row["direction"],
                            "actual_direction": row["actual_direction"],
                            "entry_price": _finite(row["price_to_beat"]),
                            "close_price": _finite(row["actual_price"]),
                            "correct": row["correct"] == 1,
                            "settlement_method": row["settlement_method"],
                            "benchmark_eligible": row["primary_benchmark_eligible"] == 1}
                           for row in recent],
            }
        return {"symbol": SYMBOL, "status": "available", "as_of_ms": now_ms,
                "scope": "paper_price_calls_no_exchange_orders", "horizons": out}
