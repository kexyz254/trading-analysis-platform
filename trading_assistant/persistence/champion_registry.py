# trading_assistant/persistence/champion_registry.py
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, List

class ChampionRegistry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_table()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS champion_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    champion_name TEXT NOT NULL,
                    promotion_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT,
                    replaced_champion TEXT,
                    sharpe REAL,
                    profit_factor REAL,
                    max_drawdown REAL,
                    folds_passed INTEGER,
                    p_value REAL,
                    extra_info TEXT
                )
            """)

    def record_promotion(
        self,
        symbol: str,
        regime: str,
        champion_name: str,
        reason: str,
        metrics: Dict,
        replaced_champion: Optional[str] = None,
    ):
        """
        Record a champion promotion event.
        metrics dict should contain keys: sharpe, profit_factor, max_drawdown,
        folds_passed, p_value (optional), and any other fields to be stored in extra_info.
        """
        with self._get_conn() as conn:
            sharpe = metrics.get('sharpe')
            profit_factor = metrics.get('profit_factor')
            max_dd = metrics.get('max_drawdown')
            folds = metrics.get('folds_passed')
            p_val = metrics.get('p_value')
            extra = {k: v for k, v in metrics.items() if k not in ('sharpe','profit_factor','max_drawdown','folds_passed','p_value')}
            conn.execute("""
                INSERT INTO champion_registry
                (symbol, regime, champion_name, reason, replaced_champion,
                 sharpe, profit_factor, max_drawdown, folds_passed, p_value, extra_info)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, regime, champion_name, reason, replaced_champion,
                sharpe, profit_factor, max_dd, folds, p_val, json.dumps(extra) if extra else None
            ))

    def get_current_champion(self, symbol: str, regime: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM champion_registry WHERE symbol=? AND regime=? ORDER BY promotion_time DESC LIMIT 1",
                (symbol, regime)
            ).fetchone()
            return dict(row) if row else None

    def get_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict]:
        with self._get_conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM champion_registry WHERE symbol=? ORDER BY promotion_time DESC LIMIT ?",
                    (symbol, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM champion_registry ORDER BY promotion_time DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]