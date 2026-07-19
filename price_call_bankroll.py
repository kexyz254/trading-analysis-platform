"""
Simulated bankroll engine for price calls – additive, standalone.
Reads settled price calls from the analytics database and simulates
trades with real market costs, partial fills, and risk limits.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------- Configuration Parameters ----------
STARTING_BANKROLL = 50.0
POSITION_SIZE_PCT = 0.10
MIN_CONFIDENCE = 0.60
FEE_RATE = 0.001
SLIPPAGE_BASE = 0.0002
SLIPPAGE_RANDOM = 0.0001
PARTIAL_FILL_PROB = 0.05
PARTIAL_FILL_PENALTY = 0.005
DAILY_LOSS_LIMIT_PCT = 0.20
BANKROLL_FLOOR = 40.0
COOLDOWN_HOURS = 24
SEED = 42

ANALYTICS_DB = "dashboard_data/analytics.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PriceCallBankroll")


@dataclass
class Trade:
    call_id: int
    symbol: str
    horizon: str
    direction: str
    entry_price: float
    exit_price: float
    confidence: float
    is_win: bool
    pnl: float
    fees: float
    slippage: float
    exit_reason: str
    bankroll_before: float
    bankroll_after: float
    settled_at: str


@dataclass
class EngineState:
    bankroll: float = STARTING_BANKROLL
    active_trade: Optional[Trade] = None
    daily_pnl: float = 0.0
    cooldown_until: Optional[datetime] = None
    max_drawdown_peak: float = STARTING_BANKROLL
    max_drawdown_val: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    last_synced_call_id: int = 0


class PriceCallBankroll:
    """Manages separate simulated bankrolls for 5m and 15m horizons."""

    def __init__(self, db_path: str = ANALYTICS_DB, seed: int = SEED, csv_dir: str = "dashboard_data"):
        self.db_path = db_path
        self.rng = np.random.default_rng(seed)
        self.csv_dir = Path(csv_dir)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.states: Dict[str, EngineState] = {
            "5m": EngineState(),
            "15m": EngineState(),
        }
        self._init_db_schema()
        self._load_last_ids()

    def _init_db_schema(self):
        """Ensures the price_calls table structure exists in a blank analytics database."""
        if not Path(self.db_path).parent.exists():
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    probability REAL,
                    price_at_call REAL,
                    actual_price REAL,
                    correct INTEGER,
                    voided INTEGER DEFAULT 0,
                    settled_at TEXT
                )
            """)
            conn.commit()

    def sync(self) -> int:
        """Process newly settled calls since last check."""
        settled = self._fetch_unprocessed()
        count = 0
        for call in settled:
            self._process_settled_call(call)
            count += 1
            horizon = call.get("horizon_label", "5m")
            if horizon in self.states:
                self.states[horizon].last_synced_call_id = max(
                    self.states[horizon].last_synced_call_id, call["id"]
                )
        if count > 0:
            self._save_last_ids()
        return count

    def get_stats(self, horizon: str) -> dict:
        state = self.states.get(horizon, EngineState())
        trades = state.trades
        if not trades:
            return {
                "Total Trades": 0, "Wins": 0, "Losses": 0, "Winrate %": 0.0,
                "Current Bankroll": round(state.bankroll, 2),
                "Starting Bankroll": STARTING_BANKROLL,
                "Net PnL $": 0.0, "Net PnL %": 0.0,
                "Max Drawdown $": 0.0, "Max Drawdown %": 0.0,
                "Avg Win $": 0.0, "Avg Loss $": 0.0,
                "Profit Factor": 0.0,
                "Total Fees $": 0.0, "Total Slippage $": 0.0,
            }

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

        return {
            "Total Trades": len(trades),
            "Wins": len(wins),
            "Losses": len(losses),
            "Winrate %": round(len(wins) / len(trades) * 100, 2),
            "Current Bankroll": round(state.bankroll, 2),
            "Starting Bankroll": STARTING_BANKROLL,
            "Net PnL $": round(state.bankroll - STARTING_BANKROLL, 2),
            "Net PnL %": round((state.bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100, 2),
            "Max Drawdown $": round(state.max_drawdown_val, 2),
            "Max Drawdown %": round(state.max_drawdown_val / STARTING_BANKROLL * 100, 2),
            "Avg Win $": round(avg_win, 4),
            "Avg Loss $": round(avg_loss, 4),
            "Profit Factor": round(profit_factor, 2),
            "Total Fees $": round(sum(t.fees for t in trades), 4),
            "Total Slippage $": round(sum(t.slippage for t in trades), 4),
        }

    def get_all_trades(self, horizon: str) -> List[dict]:
        return [self._trade_to_dict(t) for t in self.states.get(horizon, EngineState()).trades]

    def _fetch_unprocessed(self) -> List[dict]:
        min_id = min(s.last_synced_call_id for s in self.states.values())
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM price_calls
                   WHERE settled_at IS NOT NULL AND voided = 0 AND id > ?
                   ORDER BY id ASC""",
                (min_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _load_last_ids(self):
        path = self.csv_dir / "bankroll_state.csv"
        if path.exists():
            try:
                with open(path, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        h = row["horizon"]
                        if h in self.states:
                            self.states[h].last_synced_call_id = int(row["last_synced_call_id"])
            except Exception as e:
                logger.error(f"Failed to load bankroll state checkpoint: {e}")

    def _save_last_ids(self):
        path = self.csv_dir / "bankroll_state.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["horizon", "last_synced_call_id"])
            for h, state in self.states.items():
                writer.writerow([h, state.last_synced_call_id])

    def _process_settled_call(self, call: dict):
        horizon = call.get("horizon_label", "5m")
        if horizon not in self.states:
            return

        state = self.states[horizon]
        direction = call["direction"]
        confidence = call.get("probability", 0.0) or 0.0
        price_at_call = call["price_at_call"]
        actual_price = call.get("actual_price")
        settled_at = call.get("settled_at", "")

        if actual_price is None:
            return

        # Core trade logic execution criteria
        if confidence >= MIN_CONFIDENCE and state.bankroll >= BANKROLL_FLOOR:
            now_utc = datetime.now(timezone.utc)
            if state.cooldown_until and now_utc < state.cooldown_until:
                return
            if self._daily_loss_breached(state):
                state.cooldown_until = now_utc + timedelta(hours=COOLDOWN_HOURS)
                return
            
            self._simulate_trade_cycle(state, call, price_at_call, actual_price, confidence, direction, settled_at)

    def _daily_loss_breached(self, state: EngineState) -> bool:
        return state.daily_pnl <= -(STARTING_BANKROLL * DAILY_LOSS_LIMIT_PCT)

    def _simulate_trade_cycle(self, state: EngineState, call: dict, entry_price: float, 
                               exit_price: float, confidence: float, direction: str, timestamp: str):
        # Calculate dynamic entry slippage
        slip_pct_entry = SLIPPAGE_BASE + self.rng.uniform(0, SLIPPAGE_RANDOM)
        if self.rng.uniform(0, 1) < PARTIAL_FILL_PROB:
            slip_pct_entry += PARTIAL_FILL_PENALTY
        
        # Calculate dynamic exit slippage
        slip_pct_exit = SLIPPAGE_BASE + self.rng.uniform(0, SLIPPAGE_RANDOM)
        if self.rng.uniform(0, 1) < PARTIAL_FILL_PROB:
            slip_pct_exit += PARTIAL_FILL_PENALTY

        # Compute transaction impact
        if direction == "UP":
            executed_entry = entry_price * (1 + slip_pct_entry)
            executed_exit = exit_price * (1 - slip_pct_exit)
        else:
            executed_entry = entry_price * (1 - slip_pct_entry)
            executed_exit = exit_price * (1 + slip_pct_exit)

        bankroll_before = state.bankroll
        position_value = bankroll_before * POSITION_SIZE_PCT
        
        fee_entry = position_value * FEE_RATE
        fee_exit = (position_value / executed_entry) * executed_exit * FEE_RATE
        total_fees = fee_entry + fee_exit
        
        slippage_cost = (abs(executed_entry - entry_price) + abs(executed_exit - exit_price)) * (position_value / entry_price)

        # Execution direction math
        units = position_value / executed_entry
        gross_pnl = (executed_exit - executed_entry) * units if direction == "UP" else (executed_entry - executed_exit) * units
        net_pnl = gross_pnl - total_fees
        
        # State mutation
        state.bankroll += net_pnl
        state.daily_pnl += net_pnl

        # Track Max Drawdown Metrics
        if state.bankroll > state.max_drawdown_peak:
            state.max_drawdown_peak = state.bankroll
        drawdown = state.max_drawdown_peak - state.bankroll
        if drawdown > state.max_drawdown_val:
            state.max_drawdown_val = drawdown

        trade = Trade(
            call_id=call["id"],
            symbol=call["symbol"],
            horizon=call.get("horizon_label", "5m"),
            direction=direction,
            entry_price=round(executed_entry, 4),
            exit_price=round(executed_exit, 4),
            confidence=confidence,
            is_win=net_pnl > 0,
            pnl=round(net_pnl, 4),
            fees=round(total_fees, 4),
            slippage=round(slippage_cost, 4),
            exit_reason="CYCLE_MATURED",
            bankroll_before=round(bankroll_before, 2),
            bankroll_after=round(state.bankroll, 2),
            settled_at=timestamp,
        )
        state.trades.append(trade)

    def _trade_to_dict(self, t: Trade) -> dict:
        return {
            "call_id": t.call_id, "symbol": t.symbol, "horizon": t.horizon,
            "direction": t.direction, "entry_price": t.entry_price,
            "exit_price": t.exit_price, "confidence": t.confidence,
            "is_win": t.is_win, "pnl": t.pnl, "fees": t.fees,
            "bankroll_before": t.bankroll_before, "bankroll_after": t.bankroll_after,
            "settled_at": t.settled_at
        }
