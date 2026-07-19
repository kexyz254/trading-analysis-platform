"""
Self-Scoring Multi-Horizon Price Call Engine & Analytical Registry.
Tracks immediate market edges over 5m, 15m, and 30m windows, scores them
against true exchange prices upon maturity, and serves telemetry to the UI.
"""

from __future__ import annotations

import os
import sqlite3
import time
import logging
import threading
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

from webapp import data_access as da

logger = logging.getLogger("RealTimePriceEngine")

DEFAULT_ANALYTICS_DB = "dashboard_data/analytics.db"


class PriceCallBook:
    """
    Read-only API bridge consumed directly by FastAPI data routers.
    Maps historical calculations directly to the frontend display.
    """
    
    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB):
        self.db_path = db_path

    def open_call(self, symbol: str) -> List[Dict[str, Any]]:
        """Returns all un-settled, active tracking contracts for a given pair."""
        if not os.path.exists(self.db_path):
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM price_calls 
                WHERE symbol = ? AND settled_at IS NULL AND voided = 0
                ORDER BY created_at DESC
            """, (symbol,)).fetchall()
            return [dict(r) for r in rows]

    def recent_settled(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns the historical track record of scored calls for transparency verification."""
        if not os.path.exists(self.db_path):
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM price_calls 
                WHERE symbol = ? AND settled_at IS NOT NULL AND voided = 0
                ORDER BY settled_at DESC
                LIMIT ?
            """, (symbol, limit)).fetchall()
            return [dict(r) for r in rows]

    def stats(self, symbol: str) -> Dict[str, Any]:
        """Aggregates accuracy performance scores without generating placeholder metrics."""
        if not os.path.exists(self.db_path):
            return {"total_resolved": 0, "win_rate": 0.0, "avg_miss_pct": 0.0}
            
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT 
                    COUNT(*) as total_resolved,
                    SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) as wins,
                    AVG(miss_pct) as avg_miss_pct
                FROM price_calls 
                WHERE symbol = ? AND settled_at IS NOT NULL AND voided = 0
            """, (symbol,)).fetchone()
            
            if not row or row["total_resolved"] == 0:
                return {"total_resolved": 0, "win_rate": 0.0, "avg_miss_pct": 0.0}
            
            total = row["total_resolved"]
            wins = row["wins"] or 0
            return {
                "total_resolved": total,
                "win_rate": float(wins / total),
                "avg_miss_pct": float(row["avg_miss_pct"]) if row["avg_miss_pct"] is not None else 0.0
            }


class MarketMicroState:
    """
    Thread-safe, in-memory sliding window cache for high-frequency orderbook 
    snapshots and micro-ticks. Prevents unbounded RAM growth.
    """
    def __init__(self, max_ticks: int = 100):
        self.max_ticks = max_ticks
        # Structure: { symbol: [ { 'price': float, 'ts': int }, ... ] }
        self.tick_buffers: Dict[str, List[dict]] = {}
        # Structure: { symbol: { 'bids': [[price, qty], ...], 'asks': [...] } }
        self.orderbooks: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def update_tick(self, symbol: str, price: float):
        with self._lock:
            if symbol not in self.tick_buffers:
                self.tick_buffers[symbol] = []
            self.tick_buffers[symbol].append({"price": price, "ts": time.time()})
            if len(self.tick_buffers[symbol]) > self.max_ticks:
                self.tick_buffers[symbol].pop(0)

    def update_orderbook(self, symbol: str, bids: list, asks: list):
        with self._lock:
            self.orderbooks[symbol] = {"bids": bids, "asks": asks}

    def get_state(self, symbol: str) -> Tuple[dict, list]:
        with self._lock:
            return self.orderbooks.get(symbol, {"bids": [], "asks": []}), list(self.tick_buffers.get(symbol, []))


class RealTimePriceCallEngine:
    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB):
        self.db_path = db_path
        self.horizons = {
            "5m": 5 * 60,
            "15m": 15 * 60,
            "30m": 30 * 60
        }
        self._init_db()

    def _init_db(self):
        """Ensures the analytics database schema is perfectly aligned with dashboard queries."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")  # Prevent database locks during real-time writes
            conn.execute("""
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
                    confidence REAL DEFAULT 0.5
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_calls_lookup ON price_calls (symbol, horizon_label, settled_at, voided);")
            conn.commit()

    def generate_live_features(self, orderbook_depth: dict, recent_ticks: list) -> Tuple[float, float, float]:
        """
        Computes the real-time feature telemetry requested in the blueprint:
        Returns: (buyer_vs_seller_pressure, momentum, volatility)
        """
        if not recent_ticks:
            return 0.5, 0.0, 0.0
            
        # 1. Orderbook imbalance (Buyer vs Seller pressure)
        bids = sum(float(b[1]) for b in orderbook_depth.get("bids", []))
        asks = sum(float(a[1]) for a in orderbook_depth.get("asks", []))
        total_volume = bids + asks
        buyer_pressure = bids / total_volume if total_volume > 0 else 0.5

        # 2. Micro-Momentum (log return over recent streaming ticks)
        prices = [t['price'] for t in recent_ticks]
        momentum = np.log(prices[-1] / prices[0]) if len(prices) > 1 else 0.0

        # 3. Micro-Volatility (Standard deviation of returns)
        volatility = float(np.std(np.diff(prices))) if len(prices) > 2 else 0.0

        return buyer_pressure, momentum, volatility

    def evaluate_and_cast_predictions(self, symbol: str, current_price: float, orderbook_depth: dict, recent_ticks: list):
        """
        Runs real-time directional inference. If an edge is found, it commits a new 
        Price to Beat across the 5m, 15m, and 30m windows simultaneously.
        """
        buyer_pressure, momentum, volatility = self.generate_live_features(orderbook_depth, recent_ticks)
        now = int(time.time())

        for horizon, duration in self.horizons.items():
            # Check if we already have an active contract pending for this window to prevent over-allocation
            with sqlite3.connect(self.db_path) as conn:
                active_count = conn.execute(
                    "SELECT COUNT(*) FROM price_calls WHERE symbol = ? AND horizon_label = ? AND settled_at IS NULL AND voided = 0",
                    (symbol, horizon)
                ).fetchone()[0]

            if active_count > 0:
                continue  # Let the current active call run its course

            # Core Live Predictive Matrix Rules
            # Maps features into direction probabilities (Softmax Approximation)
            score = (buyer_pressure - 0.5) * 2.0 + (momentum * 100.0)
            prob_up = 1.0 / (1.0 + np.exp(-score))
            prob_down = 1.0 - prob_up
            confidence = float(abs(prob_up - prob_down))

            # Directional Classification assignment
            if prob_up > 0.55:
                direction = "UP"
            elif prob_down > 0.55:
                direction = "DOWN"
            else:
                continue # Edge insufficient -> Assign HOLD state

            due_at = now + duration

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO price_calls (
                        symbol, horizon_label, direction, price_to_beat, 
                        created_at, due_at, voided, prob_up, prob_down, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (symbol, horizon, direction, current_price, now, due_at, prob_up, prob_down, confidence))
                conn.commit()

            logger.info(f"🔮 [ENGINE PRO CALL CAST] {symbol} {horizon} -> {direction} | Price to Beat: {current_price:.2f} | Conf: {confidence:.2%}")

    def settle_expired_calls(self, symbol: str, current_price: float):
        """
        Continually monitors open prediction entries. Closes them at the exact second 
        they expire against the incoming live ticker feed.
        """
        now = int(time.time())

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            expired_calls = conn.execute("""
                SELECT id, horizon_label, direction, price_to_beat 
                FROM price_calls 
                WHERE symbol = ? AND settled_at IS NULL AND voided = 0 AND due_at <= ?
            """, (symbol, now)).fetchall()

            for call in expired_calls:
                call_id = call["id"]
                direction = call["direction"]
                price_to_beat = call["price_to_beat"]

                if direction == "UP":
                    correct = 1 if current_price > price_to_beat else 0
                else:
                    correct = 1 if current_price < price_to_beat else 0

                miss_pct = (abs(current_price - price_to_beat) / price_to_beat) * 100.0

                conn.execute("""
                    UPDATE price_calls 
                    SET actual_price = ?, correct = ?, miss_pct = ?, settled_at = ? 
                    WHERE id = ?
                """, (current_price, correct, miss_pct, now, call_id))

                logger.info(
                    f"🏆 [PRO CALC SETTLED] ID {call_id} ({symbol} {call['horizon_label']}) matured. "
                    f"Target: {direction} from {price_to_beat:.2f} -> Closed at: {current_price:.2f} | {'WIN' if correct else 'LOSS'}"
                )
            if expired_calls:
                conn.commit()


# --- FRONTEND ROUTER INSTANTIATION ANCHORS & BACKEND TUNNEL ---

# Global thread-safe micro-state engine register
state_registry = MarketMicroState()


def get_book() -> PriceCallBook:
    """Instantiates the UI reader interface requested by server.py routers."""
    return PriceCallBook()


def run_once(config: Any):
    """
    Invoked by the background task worker loop in webapp/server.py.
    Continuously updates and evaluates micro-horizon price calls against 
    the active in-memory streaming market micro-state.
    """
    engine = RealTimePriceCallEngine()
    symbols = getattr(config, "symbols", [])
    
    for symbol in symbols:
        try:
            # 1. Extract freshest high-frequency streaming metrics from in-memory state
            depth, ticks = state_registry.get_state(symbol)
            
            if not ticks:
                # Fallback to standard data access row if stream hasn't fully warmed up yet
                raw_bars = da.fetch_ohlcv(config, symbol, timeframe="1m", limit=2)
                if not raw_bars:
                    continue
                latest_price = float(raw_bars[-1][4])
            else:
                latest_price = ticks[-1]["price"]

            # 2. Settle contracts whose maturities have passed
            engine.settle_expired_calls(symbol, latest_price)

            # 3. Evaluate live features and conditionally cast new short-term contracts
            if depth.get("bids") and depth.get("asks") and len(ticks) >= 2:
                engine.evaluate_and_cast_predictions(symbol, latest_price, depth, ticks)
                
        except Exception as e:
            logger.error(f"Error executing price-call analytical execution pass for {symbol}: {e}", exc_info=True)
