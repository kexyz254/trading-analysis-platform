# trading_assistant/evaluation/recorder.py (new file)
import sqlite3
import json
from typing import Dict, Optional

class CandidatePerformanceRecorder:
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
                CREATE TABLE IF NOT EXISTS candidate_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    candidate_name TEXT NOT NULL,
                    fold_number INTEGER NOT NULL,
                    net_pnl REAL,
                    profit_factor REAL,
                    sharpe REAL,
                    max_drawdown REAL,
                    win_rate REAL,
                    num_trades INTEGER,
                    avg_trade_duration REAL,
                    extra_info TEXT,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def record_fold_metrics(
        self,
        symbol: str,
        candidate_name: str,
        fold_number: int,
        metrics: Dict,
    ):
        with self._get_conn() as conn:
            extra = {k: v for k, v in metrics.items() if k not in (
                'net_pnl', 'profit_factor', 'sharpe', 'max_drawdown',
                'win_rate', 'num_trades', 'avg_trade_duration'
            )}
            conn.execute("""
                INSERT INTO candidate_performance
                (symbol, candidate_name, fold_number, net_pnl, profit_factor,
                 sharpe, max_drawdown, win_rate, num_trades, avg_trade_duration, extra_info)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, candidate_name, fold_number,
                metrics.get('net_pnl'),
                metrics.get('profit_factor'),
                metrics.get('sharpe'),
                metrics.get('max_drawdown'),
                metrics.get('win_rate'),
                metrics.get('num_trades'),
                metrics.get('avg_trade_duration'),
                json.dumps(extra) if extra else None
            ))