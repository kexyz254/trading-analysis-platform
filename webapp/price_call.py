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

# The horizons this engine calls, each independently tracked and scored:
# (seconds-to-settle, display label, forecast timeframe the direction model
# is trained on). Both are driven by the same real per-timeframe forecast
# models (webapp.forecast) and settled against the real price at expiry.
HORIZONS = [(300, "5m", "5m"), (900, "15m", "15m")]

# A due call settled more than this long after its due time is VOIDED, not
# scored - the loop runs every ~60s, so anything later means the process was
# down and the only price available is from long after the settle mark.
SETTLE_GRACE_SECONDS = 120
# Median |per-candle move| window (bars of the forecast timeframe).
_MOVE_WINDOW_BARS = 120
_STATS_WINDOW = 50

# A call becomes a tradeable SIGNAL only when the model is this confident.
CONFIDENCE_SIGNAL_THRESHOLD = 0.80
# ...and the signal is only marked VALIDATED when this horizon has a real,
# above-coin-flip track record - so an overconfident-but-wrong model can't
# masquerade as a sweet spot.
_VALIDATED_MIN_N = 20
_VALIDATED_MIN_ACCURACY = 0.55

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
    voided INTEGER NOT NULL DEFAULT 0,
    -- Which horizon this call belongs to (5m / 15m), tracked and scored
    -- independently. Added after the first single-horizon deploy - existing
    -- rows are backfilled to the original 5m horizon (see the migration).
    horizon_seconds INTEGER NOT NULL DEFAULT 300,
    horizon_label TEXT NOT NULL DEFAULT '5m',
    -- 1 when the direction came from a promoted tournament champion
    -- (price_call_{horizon}); 0 when it came from the unvalidated heuristic
    -- forecast fallback. Lets the dashboard say which engine made the call.
    champion_backed INTEGER NOT NULL DEFAULT 0
);
"""
# The index is created AFTER the column migration in __init__, not here: on
# a legacy db it references horizon_label before the ALTER has added it.
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_price_calls_symbol_horizon ON price_calls (symbol, horizon_label, id)"


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
            # Multi-horizon columns added after the single-horizon deploy;
            # existing rows were all 5m calls, so the defaults backfill them
            # correctly (same idempotent PRAGMA + ALTER pattern as above).
            if "horizon_seconds" not in columns:
                conn.execute("ALTER TABLE price_calls ADD COLUMN horizon_seconds INTEGER NOT NULL DEFAULT 300")
            if "horizon_label" not in columns:
                conn.execute("ALTER TABLE price_calls ADD COLUMN horizon_label TEXT NOT NULL DEFAULT '5m'")
            # champion_backed added when the price-call engine learned to defer
            # to a promoted tournament champion; legacy rows were all heuristic.
            if "champion_backed" not in columns:
                conn.execute("ALTER TABLE price_calls ADD COLUMN champion_backed INTEGER NOT NULL DEFAULT 0")
            conn.execute(_INDEX_SQL)  # after the ALTERs, so the column exists
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
        horizon_seconds: int = 300,
        horizon_label: str = "5m",
        champion_backed: bool = False,
        made_at: Optional[str] = None,
        due_at: Optional[str] = None,
    ) -> int:
        made = made_at or self._now_iso()
        due = due_at or (datetime.fromisoformat(made) + timedelta(seconds=horizon_seconds)).isoformat()
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO price_calls
                    (symbol, made_at, due_at, price_at_call, direction, probability,
                     hit_rate_at_call, projected_price, expected_move_pct, horizon_seconds,
                     horizon_label, champion_backed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, made, due, price_at_call, direction, probability,
                 hit_rate_at_call, projected_price, expected_move_pct, horizon_seconds,
                 horizon_label, 1 if champion_backed else 0),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def open_call(self, symbol: str, horizon_label: str = "5m") -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM price_calls WHERE symbol = ? AND horizon_label = ? AND settled_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (symbol, horizon_label),
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

    def recent_settled(self, symbol: str, horizon_label: str = "5m", limit: int = 20) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM price_calls WHERE symbol = ? AND horizon_label = ? "
                "AND settled_at IS NOT NULL AND voided = 0 ORDER BY id DESC LIMIT ?",
                (symbol, horizon_label, limit),
            ).fetchall()
        records = [dict(row) for row in rows]
        records.reverse()
        return records

    def stats(self, symbol: str, horizon_label: str = "5m", window: int = _STATS_WINDOW) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT correct, miss_pct FROM price_calls
                WHERE symbol = ? AND horizon_label = ? AND settled_at IS NOT NULL AND voided = 0
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, horizon_label, window),
            ).fetchall()
        if not rows:
            return {"n": 0, "accuracy": None, "avg_miss_pct": None}
        n = len(rows)
        return {
            "n": n,
            "accuracy": sum(r["correct"] for r in rows) / n,
            "avg_miss_pct": sum(r["miss_pct"] for r in rows) / n,
        }


def _aligned_due_iso(horizon_seconds: int, now: Optional[datetime] = None) -> str:
    """The next WALL-CLOCK boundary for this horizon, so calls settle on the
    clock the user reads: a 5m call lands on the next :00/:05/:10… and a 15m
    call on the next :00/:15/:30/:45. Boundaries are computed from the epoch
    (UTC) but land on the same minute marks in any whole-hour-offset zone
    like East Africa Time (UTC+3), so the settle time matches the user's
    local clock exactly. If the next boundary is too close to give a
    meaningful window (< 15% of the horizon, min 60s), the one after is used
    so a call made a few seconds before a mark isn't scored on ~nothing."""
    now = now or datetime.now(timezone.utc)
    epoch = now.timestamp()
    boundary = (int(epoch // horizon_seconds) + 1) * horizon_seconds
    min_lead = max(60.0, horizon_seconds * 0.15)
    if boundary - epoch < min_lead:
        boundary += horizon_seconds
    return datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat()


def _current_price(config: Config, symbol: str) -> float:
    # The in-progress 1m candle's latest close is the freshest price the
    # existing cached REST path can give (the 5s cache TTL bounds staleness).
    raw = da.fetch_ohlcv(config, symbol, "1m", limit=2)
    if not raw:
        raise ValueError(f"no price data for {symbol}")
    return float(raw[-1]["close"])


def signal_for(open_call: Optional[Dict[str, Any]], stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A tradeable BUY/SELL signal, or None. Fires only when the model's
    confidence clears CONFIDENCE_SIGNAL_THRESHOLD; it is marked VALIDATED
    only when this horizon has a real, above-coin-flip track record, so an
    overconfident-but-historically-wrong model can't pose as a sweet spot."""
    if not open_call or open_call.get("probability") is None:
        return None
    conf = float(open_call["probability"])
    if conf < CONFIDENCE_SIGNAL_THRESHOLD:
        return None
    n = stats.get("n") or 0
    acc = stats.get("accuracy")
    validated = n >= _VALIDATED_MIN_N and acc is not None and acc >= _VALIDATED_MIN_ACCURACY
    if n < _VALIDATED_MIN_N:
        note = f"unproven — only {n} settled call{'s' if n != 1 else ''} so far"
    elif validated:
        note = f"validated — {acc * 100:.0f}% correct over last {n}"
    else:
        note = f"caution — only {acc * 100:.0f}% correct over last {n} (near coin-flip)"
    return {
        "side": "BUY" if open_call["direction"] == "UP" else "SELL",
        "confidence": conf,
        "validated": validated,
        "note": note,
    }


# How much 15m history the champion model trains on per call, and the floor
# below which there isn't enough to train honestly. The champion's look-ahead
# (horizon_bars) is NOT hardcoded here - it is read from the candidate's own
# registered params (see _champion_horizon_bars).
_CHAMPION_HISTORY_BARS = 600
_CHAMPION_MIN_BARS = 200
# Default trading-system db the tournament writes its promotions to. The
# dashboard is a pure READER of this file (never a writer - see the storage
# note at the top of this module); server.py threads its own --db path in.
DEFAULT_TRADING_DB = "trading_system.db"


@lru_cache(maxsize=8)
def _champion_horizon_bars(horizon_label: str) -> int:
    """The look-ahead (in 15m bars) for this horizon's price-call champion,
    read from the candidate's registered params so it matches the tournament
    exactly and is never hardcoded in two places."""
    from trading_assistant.agents.strategy_manager import default_candidates

    name = f"price_call_{horizon_label}"
    for candidate in default_candidates():
        if candidate.name == name:
            return int(candidate.params.get("horizon_bars", 1))
    return 1


def get_champion_prediction(
    config: Config,
    symbol: str,
    horizon_label: str,
    *,
    journal: Optional[Any] = None,
    trading_db_path: str = DEFAULT_TRADING_DB,
) -> Optional[Dict[str, Any]]:
    """The promoted price-call champion's live prediction for (symbol,
    horizon), or None when no champion is promoted.

    A price_call_{horizon} candidate is 'promoted' exactly when the tournament
    has selected it as the winner for at least one regime in the symbol's
    latest tournament event - i.e. it cleared the identical walk-forward
    promotion bar every candidate must clear. When that holds, we train the
    candidate's own calibrated up/down models on live 15m history and return
    the latest closed bar's probability. When it doesn't, we return None and
    the caller falls back to the unvalidated heuristic.

    Reads the trading-system journal READ-ONLY (initialize=False); it never
    writes to that database.
    """
    candidate_name = f"price_call_{horizon_label}"

    j = journal
    if j is None:
        from trading_assistant.persistence.journal import Journal

        j = Journal(trading_db_path, initialize=False)
    try:
        event = j.latest_tournament_event(symbol)
    except Exception:  # noqa: BLE001 - a schema-behind / missing journal is just "no champion"
        return None
    if not event:
        return None
    winners = event.get("winners") or {}
    promoted = any(
        (sel or {}).get("winner") == candidate_name for sel in winners.values()
    )
    if not promoted:
        return None

    # Champion is promoted -> produce its real prediction from live 15m data.
    import pandas as pd
    from trading_assistant.strategies.price_call_candidate import predict_live_probability

    raw = da.fetch_ohlcv(config, symbol, "15m", limit=_CHAMPION_HISTORY_BARS)
    if not raw or len(raw) < _CHAMPION_MIN_BARS:
        return None
    frame = pd.DataFrame(raw)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    frame = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    frame = frame.iloc[:-1]  # drop the in-progress candle; predict from the last CLOSED bar

    horizon_bars = _champion_horizon_bars(horizon_label)
    pred = predict_live_probability(frame, {"horizon_bars": horizon_bars})
    if pred is None:
        return None
    p_up = float(pred["probability_up"])
    return {
        "probability_up": p_up,
        "direction": "UP" if p_up >= 0.5 else "DOWN",
        "confidence": max(p_up, 1.0 - p_up),
        "champion_name": candidate_name,
        "trained_rows": int(pred["trained_rows"]),
    }


def _make_call(config: Config, book: PriceCallBook, symbol: str,
               horizon_seconds: int, horizon_label: str, forecast_tf: str,
               *, journal: Optional[Any] = None,
               trading_db_path: str = DEFAULT_TRADING_DB) -> int:
    from webapp.forecast import get_forecast

    forecast = get_forecast(config, symbol, forecast_tf)
    next_candle = forecast["horizons"][0]

    raw = da.fetch_ohlcv(config, symbol, forecast_tf, limit=_MOVE_WINDOW_BARS)
    closes = [b["close"] for b in raw[:-1]]  # closed candles only
    if len(closes) < 20:
        raise ValueError(f"not enough {forecast_tf} history for {symbol}")
    moves = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    expected_move_pct = statistics.median(moves)

    price_now = _current_price(config, symbol)

    # Prefer the promoted champion; fall back to the heuristic forecast when no
    # champion is promoted for this (symbol, horizon). The magnitude is always
    # the typical-move median - both engines predict DIRECTION only.
    champion = None
    try:
        champion = get_champion_prediction(
            config, symbol, horizon_label,
            journal=journal, trading_db_path=trading_db_path,
        )
    except Exception:  # noqa: BLE001 - champion lookup must never block a heuristic call
        logger.exception("champion lookup failed for %s %s", symbol, horizon_label)

    if champion is not None:
        direction = champion["direction"]
        probability = champion["confidence"]
        champion_backed = True
    else:
        p_up = next_candle["probability_up"]
        direction = "UP" if p_up >= 0.5 else "DOWN"
        probability = max(p_up, 1 - p_up)
        champion_backed = False

    up = direction == "UP"
    projected = price_now * (1 + expected_move_pct) if up else price_now * (1 - expected_move_pct)

    return book.create(
        symbol=symbol,
        price_at_call=price_now,
        direction=direction,
        probability=probability,
        hit_rate_at_call=next_candle["hit_rate"],
        projected_price=projected,
        expected_move_pct=expected_move_pct,
        horizon_seconds=horizon_seconds,
        horizon_label=horizon_label,
        champion_backed=champion_backed,
        # Settle on the next wall-clock boundary (e.g. next :05 / :15), not
        # "now + horizon", so the call matches the clock time the user sees.
        due_at=_aligned_due_iso(horizon_seconds),
    )


def run_once(config: Config, db_path: str = DEFAULT_ANALYTICS_DB, symbols: Optional[List[str]] = None,
             trading_db_path: str = DEFAULT_TRADING_DB) -> None:
    """One pass of the call loop: settle everything due, then open a fresh
    call for any (symbol, horizon) without one. Runs every ~60s from the
    dashboard's background task, so settlement lands within a minute of due
    time and a new call opens right after the old one settles - one call per
    symbol per horizon (5m, 15m), each tracked and scored independently.

    Calls are made only for the config seed symbols, not the full discovery
    universe: each costs a cached forecast model and the dashboard container
    has a hard memory cap.
    """
    book = get_book(db_path)
    now = datetime.now(timezone.utc)

    # Build one read-only journal for the whole pass so the champion lookup
    # doesn't reopen the trading-system db once per (symbol, horizon).
    journal = None
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(trading_db_path, initialize=False)
    except Exception:  # noqa: BLE001 - no journal just means "no champion", stay on heuristic
        logger.exception("could not open trading journal at %s for champion lookup", trading_db_path)

    for call in book.due_unsettled():
        try:
            overdue = (now - datetime.fromisoformat(call["due_at"])).total_seconds()
            if overdue > SETTLE_GRACE_SECONDS:
                # Came due while the process was down - the only price we have
                # is from far past the settle mark; scoring it would
                # contaminate the track record, so void it instead.
                book.void(call["id"])
                logger.info("voided price call %s (%.0fs overdue)", call["id"], overdue)
                continue
            book.settle(call["id"], _current_price(config, call["symbol"]))
        except Exception:  # noqa: BLE001 - one symbol's failure must not block the rest
            logger.exception("failed to settle price call %s", call["id"])

    for symbol in symbols or config.symbols:
        for horizon_seconds, label, forecast_tf in HORIZONS:
            try:
                if book.open_call(symbol, label) is None:
                    _make_call(config, book, symbol, horizon_seconds, label, forecast_tf,
                               journal=journal, trading_db_path=trading_db_path)
            except Exception:  # noqa: BLE001
                logger.exception("failed to make %s price call for %s", label, symbol)
