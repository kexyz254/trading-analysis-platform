"""Compatibility reader for the authoritative SQLite paper-bankroll ledger.

Settlement now happens transactionally in :mod:`webapp.price_call`.  This
facade intentionally performs no replay, random slippage, or CSV checkpointing.
"""

from __future__ import annotations

from webapp.price_call import (
    DEFAULT_ANALYTICS_DB,
    PriceCallBook,
    RealTimePriceCallEngine,
)


class PriceCallBankroll:
    """Backward-compatible, read-only view of paper performance."""

    def __init__(self, db_path: str = DEFAULT_ANALYTICS_DB, **_ignored):
        RealTimePriceCallEngine(db_path)
        self.book = PriceCallBook(db_path)

    def sync(self) -> int:
        """Retained for callers; stream settlement is already synchronous."""
        return 0

    def get_stats(self, horizon: str) -> dict:
        report = self.book.bankroll_report()["horizons"][horizon]
        return {
            "Total Trades": report["settlements"],
            "Wins": report["net_wins"],
            "Losses": report["net_losses"],
            "Winrate %": report["net_win_rate"] * 100,
            "Current Bankroll": report["settled_balance"],
            "Starting Bankroll": report["starting_balance"],
            "Net PnL $": report["net_pnl"],
            "Net PnL %": report["net_return_pct"],
            "Max Drawdown $": report["max_drawdown"],
            "Max Drawdown %": report["max_drawdown_pct"],
            "Profit Factor": report["profit_factor"],
            "Total Fees $": report["total_fees"],
            "Total Slippage $": report["total_slippage"],
            "Open Exposure $": report["open_exposure"],
            "Net Expectancy $": report["net_expectancy"],
        }

    def get_all_trades(self, horizon: str) -> list[dict]:
        return self.book.bankroll_report(recent_limit=100)["horizons"][horizon][
            "recent_settlements"
        ][::-1]
