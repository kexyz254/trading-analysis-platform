# trading_assistant/persistence/experiment_log.py
import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

class ExperimentLog:
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
                CREATE TABLE IF NOT EXISTS experiment_log (
                    experiment_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    changes TEXT,
                    status TEXT DEFAULT 'running',
                    result_sharpe REAL,
                    result_profit_factor REAL,
                    result_max_drawdown REAL,
                    result_net_pnl REAL,
                    result_win_rate REAL,
                    decision TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def start_experiment(self, experiment_id: str, goal: str, changes: Optional[str] = None):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO experiment_log (experiment_id, goal, changes, status) VALUES (?, ?, ?, 'running')",
                (experiment_id, goal, changes)
            )

    def record_result(self, experiment_id: str, metrics: Dict):
        """Update the experiment with result metrics."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE experiment_log
                SET result_sharpe = ?,
                    result_profit_factor = ?,
                    result_max_drawdown = ?,
                    result_net_pnl = ?,
                    result_win_rate = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE experiment_id = ?
            """, (
                metrics.get('sharpe'),
                metrics.get('profit_factor'),
                metrics.get('max_drawdown'),
                metrics.get('net_pnl'),
                metrics.get('win_rate'),
                experiment_id
            ))

    def conclude_experiment(self, experiment_id: str, decision: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE experiment_log SET status='completed', decision=?, updated_at=CURRENT_TIMESTAMP WHERE experiment_id=?",
                (decision, experiment_id)
            )

    def get_experiment(self, experiment_id: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM experiment_log WHERE experiment_id=?", (experiment_id,)).fetchone()
            return dict(row) if row else None

    def list_experiments(self, status: Optional[str] = None, limit: int = 50) -> List[Dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute("SELECT * FROM experiment_log WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM experiment_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]