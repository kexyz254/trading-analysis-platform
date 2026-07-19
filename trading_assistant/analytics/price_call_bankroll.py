"""
Simulated bankroll engine for price calls – additive, standalone.
Reads settled price calls from the existing analytics database and simulates
trades with real market costs, partial fills, and risk limits.

Usage (standalone, from the command line):
    python trading_assistant/analytics/price_call_bankroll.py

Or from code:
    from trading_assistant.analytics.price_call_bankroll import PriceCallBankroll
    engine = PriceCallBankroll()
    engine.sync()            # catch up on all settled calls
    stats = engine.get_stats("5m")
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

# ---------- Configuration (self-contained, no imports from the main system) ----------
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

# This module reads from the EXISTING analytics database.
# It never writes to it.
ANALYTICS_DB = "dashboard_data/analytics.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
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
    last_synced_call_id: int = 0  # tracks the latest settled call we've processed


class PriceCallBankroll:
    """Simulated bankroll for price calls. Reads settled calls from the
    analytics database and simulates entering/exiting trades."""

    def __init__(
        self,
        db_path: str = ANALYTICS_DB,
        seed: int = SEED,
        csv_dir: str = ".",
    ):
        self.db_path = db_path
        self.rng = np.random.default_rng(seed)
        self.csv_dir = Path(csv_dir)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.states: Dict[str, EngineState] = {
            "5m": EngineState(),
            "15m": EngineState(),
        }
        self._init_csvs()
        self._load_last_ids()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def sync(self) -> int:
        """Process all newly settled calls since the last sync.
        Returns the number of new calls processed."""
        settled = self._fetch_unprocessed()
        count = 0
        for call in settled:
            self._process_settled_call(call)
            count += 1
            for state in self.states.values():
                state.last_synced_call_id = max(state.last_synced_call_id, call["id"])
            self._save_last_ids()
        return count

    def get_stats(self, horizon: str) -> dict:
        """Return a dict of all performance metrics for a horizon."""
        state = self.states[horizon]
        trades = state.trades
        if not trades:
            return {
                "Total Trades": 0, "Wins": 0, "Losses": 0, "Winrate %": 0.0,
                "Current Bankroll": state.bankroll,
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
        """Return all trades for a horizon as a list of dicts."""
        return [self._trade_to_dict(t) for t in self.states[horizon].trades]

    # ------------------------------------------------------------------
    # Internal: database
    # ------------------------------------------------------------------
    def _fetch_unprocessed(self) -> List[dict]:
        """Fetch settled, non-voided calls we haven't processed yet."""
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
        """Restore the last synced id from a checkpoint file."""
        path = self.csv_dir / "bankroll_state.csv"
        if path.exists():
            with open(path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    horizon = row["horizon"]
                    if horizon in self.states:
                        self.states[horizon].last_synced_call_id = int(row["last_synced_call_id"])

    def _save_last_ids(self):
        """Persist the last synced id so restarts don't replay old calls."""
        path = self.csv_dir / "bankroll_state.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["horizon", "last_synced_call_id"])
            for h, state in self.states.items():
                writer.writerow([h, state.last_synced_call_id])

    # ------------------------------------------------------------------
    # Internal: trade logic
    # ------------------------------------------------------------------
    def _process_settled_call(self, call: dict):
        """Process a single settled price call: enter or exit a simulated trade."""
        horizon = call.get("horizon_label", "5m")
        if horizon not in self.states:
            return
        state = self.states[horizon]
        direction = call["direction"]
        confidence = call.get("probability", 0.0) or 0.0
        price_at_call = call["price_at_call"]
        actual_price = call.get("actual_price")
        if actual_price is None:
            return
        correct = bool(call.get("correct"))
        settled_at = call.get("settled_at", "")

        if state.active_trade is not None:
            self._close_trade(state, actual_price, correct, settled_at)

        if confidence > MIN_CONFIDENCE and state.bankroll >= BANKROLL_FLOOR:
            if state.cooldown_until and datetime.now(timezone.utc) < state.cooldown_until:
                return
            if self._daily_loss_breached(state):
                state.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)
                return
            self._enter_trade(state, call, price_at_call, confidence, direction, settled_at)

    def _enter_trade(self, state: EngineState, call: dict, entry_price: float,
                     confidence: float, direction: str, timestamp: str):
        entry, slip = self._apply_slippage(entry_price, direction)
        fee_entry = entry * POSITION_SIZE_PCT * state.bankroll * FEE_RATE
        trade = Trade(
            call_id=call["id"],
            symbol=call["symbol"],
            horizon=call.get("horizon_label", "5m"),
            direction=direction,
            entry_price=entry,
            exit_price=0.0,
            confidence=confidence,
            is_win=False,
            pnl=0.0,
            fees=fee_entry,
            slippage=slip,
            exit_reason="",
            bankroll_before=state.bankroll,
            bankroll_after=state.bankroll,
            settled_at=timestamp,
        )
        state.bankroll -= fee_entry
        state.active_trade = trade

    def _close_trade(self, state: EngineState, exit_price: float, correct: bool, settled_at: str):
        trade = state.active_trade
        if trade is None:
            return
        exit, slip_exit = self._apply_slippage(exit_price, trade.direction)
        fee_exit = exit * POSITION_SIZE_PCT * state.bankroll * FEE_RATE
        position_size = (state.bankroll * POSITION_SIZE_PCT) / trade.entry_price
        gross_pnl = (exit - trade.entry_price) * position_size
        if trade.direction == "DOWN":
            gross_pnl = -gross_pnl

        trade.exit_price = exit
        trade.is_win = gross_pnl > 0
        trade.pnl = gross_pnl - fee_exit
        trade.fees += fee_exit
        trade.slippage += slip_exit
        trade.exit_reason = "target_hit" if correct else "settled_wrong"
        trade.bankroll_after = state.bankroll + trade.pnl

        state.bankroll += trade.pnl
        state.daily_pnl += trade.pnl

        if state.bankroll > state.max_drawdown_peak:
            state.max_drawdown_peak = state.bankroll
        dd = state.max_drawdown_peak - state.bankroll
        if dd > state.max_drawdown_val:
            state.max_drawdown_val = dd

        state.trades.append(trade)
        state.active_trade = None
        self._save_trade(trade)

    def _apply_slippage(self, price: float, direction: str) -> Tuple[float, float]:
        if self.rng.random() < PARTIAL_FILL_PROB:
            slippage_pct = SLIPPAGE_BASE + PARTIAL_FILL_PENALTY
        else:
            slippage_pct = SLIPPAGE_BASE + self.rng.uniform(-SLIPPAGE_RANDOM, SLIPPAGE_RANDOM)
        if direction == "UP":
            adjusted = price * (1 + slippage_pct)
        else:
            adjusted = price * (1 - slippage_pct)
        return adjusted, abs(adjusted - price)

    def _daily_loss_breached(self, state: EngineState) -> bool:
        return state.daily_pnl < -DAILY_LOSS_LIMIT_PCT * STARTING_BANKROLL

    # ------------------------------------------------------------------
    # CSV persistence
    # ------------------------------------------------------------------
    def _init_csvs(self):
        header = [
            "call_id", "symbol", "horizon", "direction", "entry_price", "exit_price",
            "confidence", "is_win", "pnl", "fees", "slippage", "exit_reason",
            "bankroll_before", "bankroll_after", "settled_at",
        ]
        for h in ["5m", "15m"]:
            path = self.csv_dir / f"bankroll_trades_{h}.csv"
            if not path.exists():
                with open(path, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(header)

    def _save_trade(self, trade: Trade):
        path = self.csv_dir / f"bankroll_trades_{trade.horizon}.csv"
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.call_id, trade.symbol, trade.horizon, trade.direction,
                round(trade.entry_price, 4), round(trade.exit_price, 4),
                round(trade.confidence, 4), trade.is_win,
                round(trade.pnl, 6), round(trade.fees, 6),
                round(trade.slippage, 6), trade.exit_reason,
                round(trade.bankroll_before, 2), round(trade.bankroll_after, 2),
                trade.settled_at,
            ])

    @staticmethod
    def _trade_to_dict(t: Trade) -> dict:
        return {
            "call_id": t.call_id,
            "symbol": t.symbol,
            "horizon": t.horizon,
            "direction": t.direction,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "confidence": round(t.confidence, 2),
            "is_win": t.is_win,
            "pnl": round(t.pnl, 4),
            "fees": round(t.fees, 4),
            "slippage": round(t.slippage, 4),
            "exit_reason": t.exit_reason,
            "settled_at": t.settled_at,
        }


# ---------- Standalone runner ----------
if __name__ == "__main__":
    engine = PriceCallBankroll()
    n = engine.sync()
    print(f"Processed {n} new settled calls.")
    for h in ("5m", "15m"):
        stats = engine.get_stats(h)
        print(f"\n{h}: {stats['Total Trades']} trades, "
              f"bankroll ${stats['Current Bankroll']:.2f}, "
              f"winrate {stats['Winrate %']:.1f}%")