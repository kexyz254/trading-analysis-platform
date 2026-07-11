"""Distinguishing trading-driven capital changes from external ones.

A live account's free balance changes for two fundamentally different
reasons: the bot's own trading (buys spend cash, sells return it, with a
realized gain or loss), and everything else the account holder does outside
the bot - deposits, withdrawals, transfers to another product entirely (the
real incident this module exists for: moving funds into Binance's
Predictions feature dropped free balance by 99.6% and tripped the drawdown
circuit breaker as if it were a catastrophic trading loss).

Professional portfolio accounting always separates these (see: Modified
Dietz / time-weighted return, which exists specifically so a fund's
performance number isn't contaminated by investor deposits/withdrawals). The
same principle applies here: RiskAgent's drawdown-based circuit breaker must
react to trading performance, not to account activity that has nothing to
do with the bot's own decisions.
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this, an "unexplained" gap between two balance syncs is treated as
# ordinary noise (fee rounding, a fee paid in a third currency like BNB that
# never shows up in the quote-currency delta) rather than a real external
# flow - the larger of a small fixed floor and a percentage of capital, so
# this scales sensibly whether capital is $8 or $80,000.
DEFAULT_MIN_UNEXPLAINED_USD = 0.50
DEFAULT_MIN_UNEXPLAINED_PCT = 0.05


@dataclass
class CashFlowClassification:
    raw_delta: float
    trading_delta: float
    unexplained_delta: float
    is_external_flow: bool


def classify_balance_change(
    previous_capital: float,
    new_capital: float,
    trading_cash_flow_since_last_sync: float,
    min_unexplained_usd: float = DEFAULT_MIN_UNEXPLAINED_USD,
    min_unexplained_pct: float = DEFAULT_MIN_UNEXPLAINED_PCT,
) -> CashFlowClassification:
    """`trading_cash_flow_since_last_sync` is the net cash effect of every
    buy/sell the bot itself executed since the last sync (negative for
    buys, positive for sell proceeds) - NOT just realized PnL, since a buy
    moves cash into a position without any PnL yet, and that outflow must
    still count as trading-explained, not external."""
    raw_delta = new_capital - previous_capital
    unexplained_delta = raw_delta - trading_cash_flow_since_last_sync
    threshold = max(min_unexplained_usd, abs(previous_capital) * min_unexplained_pct)
    return CashFlowClassification(
        raw_delta=raw_delta,
        trading_delta=trading_cash_flow_since_last_sync,
        unexplained_delta=unexplained_delta,
        is_external_flow=abs(unexplained_delta) > threshold,
    )
