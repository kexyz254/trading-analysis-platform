"""Self-scoring 5-minute price calls for the pro dashboard.

Every 5 minutes, for each configured symbol, this records a "call": the
current price, a direction (from the same 5m-timeframe model the forecast
card uses), and a projected target price = current price moved by the
symbol's TYPICAL recent 5-minute move (median absolute close-to-close
change) in the predicted direction. The magnitude is deliberately a
median, not a model output - the models predict direction only, and
dressing a direction call up as a precise price prediction would be
fabrication.

What makes this usable rather than decorative: every call is SETTLED
against the real price when its 5 minutes are up, and the API returns the
running score (percent correct, average miss) alongside the current call.
The track record accumulates 24/7 in the background regardless of whether
anyone has the dashboard open.

Storage note - this MUST live in its own database file, never in
trading_system.db. docker-compose bind-mounts trading_system.db as a
single FILE into both containers, and SQLite's WAL mode keeps its
write-ahead log in sidecar files (-wal/-shm) next to the database - which
under a single-file mount means each container's own private overlay
filesystem, not the shared host file. Two containers committing through
two divergent private WALs into one main file corrupts it ("database disk
image is malformed" - happened in production on 2026-07-16 the moment
this module briefly made the dashboard a second writer to the shared db).
This module's database lives in a DIRECTORY mount owned solely by the
dashboard container: one writer, and the WAL sidecars travel with the db
inside the mounted directory, which is the supported setup.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
from functools import lru_cache
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_assistant.config import Config
from webapp import data_access as da

logger = logging.getLogger(__name__)

CALL_HORIZON_SECONDS = 300
# A due call settled more than this long after its due time is VOIDED, not
# scored - the loop runs every ~60s, so anything later than this means the
# process was down and the only available price is from long after the
# 5-minute mark.
SETTLE_GRACE_SECONDS = 120
# Median |5m move| is computed over this many recent closed candles.
_MOVE_WINDOW_BARS = 120
_STATS_WINDOW = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    made_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    price_at_call REAL NOT NULL,
    direction TEXT NOT NULL,
    probability REAL,
    hit_rate_at_call REAL,
    projected_price REAL NOT NULL,
    expected_move_pct REAL NOT NULL,
    actual_price REAL,
    correct INTEGER,
    miss_pct REAL,
    settled_at TEXT,
    -- 1 = call could not be settled honestly (came due while the process
    -- was down, so the only available price is from far after the 5-minute
    -- mark). Voided calls are excluded from stats and the results strip -
    -- scoring a "5-minute" call against a price from hours later would
    -- contaminate the exact track record this table exists to keep honest.
    voided INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_price_calls_symbol_id ON price_calls (symbol, id);
"""


# Default path for the dashboard-owned analytics database - inside the
# ./dashboard_data directory mount (see docker-compose.yml). Overridable
# for tests via the constructor.
DEFAULT_ANALYTICS_DB = "dashboard_data/analytics.db"


@lru_cache(maxsize=4)
def get_book(db_path: str = DEFAULT_ANALYTICS_DB) -> "PriceCallBook":
    """One book per db path: constructing PriceCallBook runs the schema
    script, and both the 60s background loop and every /api/pro/price-calls
    request need a book - re-running DDL on each of those is pure waste."""
    return PriceCallBook(db_path)


class PriceCallBook:
    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB):
        self.db_path = db_path
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            # `voided` was added after the first deploy created this table -
            # CREATE TABLE IF NOT EXISTS is a no-op against an existing db,
            # so an explicit ALTER is needed (same pattern as Journal._migrate).
            columns = {row[1] for row in conn.execute("PRAGMA table_info(price_calls)").fetchall()}
            if "voided" not in columns:
                conn.execute("ALTER TABLE price_calls ADD COLUMN voided INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(
        self,
        *,
        symbol: str,
        price_at_call: float,
        direction: str,
        probability: Optional[float],
        hit_rate_at_call: Optional[float],
        projected_price: float,
        expected_move_pct: float,
        made_at: Optional[str] = None,
        due_at: Optional[str] = None,
    ) -> int:
        made = made_at or self._now_iso()
        due = due_at or (
            datetime.fromisoformat(made) + timedelta(seconds=CALL_HORIZON_SECONDS)
        ).isoformat()
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO price_calls
                    (symbol, made_at, due_at, price_at_call, direction, probability,
                     hit_rate_at_call, projected_price, expected_move_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, made, due, price_at_call, direction, probability,
                 hit_rate_at_call, projected_price, expected_move_pct),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def open_call(self, symbol: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM price_calls WHERE symbol = ? AND settled_at IS NULL ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return dict(row) if row is not None else None

    def due_unsettled(self, now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        now = now_iso or self._now_iso()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM price_calls WHERE settled_at IS NULL AND due_at <= ? ORDER BY id ASC",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def void(self, call_id: int, settled_at: Optional[str] = None) -> None:
        """Close a call WITHOUT scoring it - see the voided column's schema
        comment for when this is the honest outcome."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE price_calls SET voided = 1, settled_at = ? WHERE id = ? AND settled_at IS NULL",
                (settled_at or self._now_iso(), call_id),
            )
            conn.commit()

    def settle(self, call_id: int, actual_price: float, settled_at: Optional[str] = None) -> None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM price_calls WHERE id = ?", (call_id,)).fetchone()
            if row is None or row["settled_at"] is not None:
                return
            went_up = actual_price > row["price_at_call"]
            # An exactly-flat price settles as incorrect for both directions:
            # a directional call that produced no move earned no credit.
            correct = 1 if (
                (row["direction"] == "UP" and went_up)
                or (row["direction"] == "DOWN" and actual_price < row["price_at_call"])
            ) else 0
            miss_pct = abs(actual_price - row["projected_price"]) / row["price_at_call"]
            conn.execute(
                "UPDATE price_calls SET actual_price = ?, correct = ?, miss_pct = ?, settled_at = ? WHERE id = ?",
                (actual_price, correct, miss_pct, settled_at or self._now_iso(), call_id),
            )
            conn.commit()

    def recent_settled(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM price_calls WHERE symbol = ? AND settled_at IS NOT NULL AND voided = 0 "
                "ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        records = [dict(row) for row in rows]
        records.reverse()
        return records

    def stats(self, symbol: str, window: int = _STATS_WINDOW) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT correct, miss_pct FROM price_calls
                WHERE symbol = ? AND settled_at IS NOT NULL AND voided = 0
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, window),
            ).fetchall()
        if not rows:
            return {"n": 0, "accuracy": None, "avg_miss_pct": None}
        n = len(rows)
        return {
            "n": n,
            "accuracy": sum(r["correct"] for r in rows) / n,
            "avg_miss_pct": sum(r["miss_pct"] for r in rows) / n,
        }


def _current_price(config: Config, symbol: str) -> float:
    # The in-progress 1m candle's latest close is the freshest price the
    # existing cached REST path can give (the 5s cache TTL bounds staleness).
    raw = da.fetch_ohlcv(config, symbol, "1m", limit=2)
    if not raw:
        raise ValueError(f"no price data for {symbol}")
    return float(raw[-1]["close"])


def _make_call(config: Config, book: PriceCallBook, symbol: str) -> int:
    from webapp.forecast import get_forecast

    forecast = get_forecast(config, symbol, "5m")
    next_candle = forecast["horizons"][0]

    raw = da.fetch_ohlcv(config, symbol, "5m", limit=_MOVE_WINDOW_BARS)
    closes = [b["close"] for b in raw[:-1]]  # closed candles only
    if len(closes) < 20:
        raise ValueError(f"not enough 5m history for {symbol}")
    moves = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    expected_move_pct = statistics.median(moves)

    price_now = _current_price(config, symbol)
    p_up = next_candle["probability_up"]
    up = p_up >= 0.5
    projected = price_now * (1 + expected_move_pct) if up else price_now * (1 - expected_move_pct)

    return book.create(
        symbol=symbol,
        price_at_call=price_now,
        direction="UP" if up else "DOWN",
        probability=max(p_up, 1 - p_up),
        hit_rate_at_call=next_candle["hit_rate"],
        projected_price=projected,
        expected_move_pct=expected_move_pct,
    )


def run_once(config: Config, db_path: str = DEFAULT_ANALYTICS_DB, symbols: Optional[List[str]] = None) -> None:
    """One pass of the call loop: settle everything due, then open a new
    call for any symbol without one. Runs every ~60s from the dashboard's
    background task, so settlement lands within a minute of due time
    (settled_at records the actual moment) and a new call opens right
    after the old one settles - one call per symbol per ~5 minutes.

    Calls are made only for the config seed symbols, not the full
    discovery universe: each symbol costs a 5m model (trained/cached by
    webapp.forecast) and the dashboard container has a hard memory cap.
    """
    book = get_book(db_path)
    now = datetime.now(timezone.utc)
    for call in book.due_unsettled():
        try:
            overdue = (now - datetime.fromisoformat(call["due_at"])).total_seconds()
            if overdue > SETTLE_GRACE_SECONDS:
                # Came due while the process was down - the only price we
                # have is from far past the 5-minute mark. Scoring it would
                # contaminate the track record; void it instead.
                book.void(call["id"])
                logger.info("voided price call %s (%.0fs overdue)", call["id"], overdue)
                continue
            book.settle(call["id"], _current_price(config, call["symbol"]))
        except Exception:  # noqa: BLE001 - one symbol's failure must not block the rest
            logger.exception("failed to settle price call %s", call["id"])

    for symbol in symbols or config.symbols:
        try:
            if book.open_call(symbol) is None:
                _make_call(config, book, symbol)
        except Exception:  # noqa: BLE001
            logger.exception("failed to make price call for %s", symbol)
