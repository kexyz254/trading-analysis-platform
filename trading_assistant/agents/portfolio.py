"""PortfolioAgent: reconciliation, mark-to-market equity tracking, and journal persistence."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from trading_assistant.agents.protective_stops import find_open_protective_stop, place_protective_stop
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.cash_flows import CashFlowClassification, classify_balance_change
from trading_assistant.portfolio.exposure import PortfolioExposureSummary, summarize_portfolio_exposure
from trading_assistant.portfolio.manager import PortfolioManager

logger = logging.getLogger(__name__)


class PortfolioAgent:
    """Wraps PortfolioManager, adding mark-to-market equity updates every cycle.

    PortfolioManager.equity_curve previously only appended on close_position,
    so unrealized PnL never showed up in drawdown/Sharpe calculations between
    trades - a risk agent watching real-time drawdown would be blind to an
    open position bleeding money. mark_to_market() closes that gap.
    """

    def __init__(self, portfolio: PortfolioManager, journal: Optional[Journal] = None, is_live: bool = False):
        self.portfolio = portfolio
        self.journal = journal
        # Tags every trade/equity_snapshot this agent writes with which
        # mode produced it - real testnet activity and real LIVE (real
        # money) activity must never be silently mixed in the same
        # "real portfolio performance" view (see Journal.record_trade's
        # is_live param docstring). Set once at construction, same
        # lifetime as Orchestrator.is_live - this process is either live
        # or it isn't, for its whole run.
        self.is_live = is_live
        # Cumulative net cash effect of every buy/sell THIS agent has
        # recorded (negative for buys, positive for sell proceeds) - the
        # running baseline sync_capital_with_exchange compares each new
        # exchange balance against, to tell "the bot traded" apart from
        # "the account holder moved money" (deposit, withdrawal, a transfer
        # to another product entirely). See portfolio.cash_flows.
        self._net_trading_cash_flow: float = 0.0
        self._net_trading_cash_flow_at_last_sync: float = 0.0
        # The first sync compares the real exchange balance against
        # initial_capital - a deliberately fictional starting number on a
        # fresh live account (see sync_capital_with_exchange's docstring),
        # not a real prior balance. That first correction is never a real
        # external flow and must not be classified as one.
        self._has_synced_before: bool = False

    @property
    def capital(self) -> float:
        return self.portfolio.capital

    def mark_to_market(self, current_prices: Dict[str, float]) -> float:
        total_value = self.portfolio.get_total_value(current_prices)
        self.portfolio.append_equity_point(total_value)
        if self.journal is not None:
            positions = {
                symbol: {
                    "quantity": pos["quantity"],
                    "price": current_prices.get(symbol, pos["price"]),
                    # Surfaced so the dashboard can flag balances this
                    # system never actually traded into (faucet-seeded or
                    # otherwise unexplained by trade history) instead of
                    # silently presenting them as strategy positions.
                    "unexplained_quantity": pos.get("unexplained_quantity", 0.0),
                }
                for symbol, pos in self.portfolio.positions.items()
            }
            self.journal.record_equity_snapshot(
                capital=self.portfolio.capital, total_equity=total_value, positions=positions, is_live=self.is_live
            )
        return total_value

    def positions_frame(self, current_prices: Dict[str, float]) -> pd.DataFrame:
        rows = [
            {"symbol": symbol, "quantity": pos["quantity"], "price": current_prices.get(symbol, pos["price"])}
            for symbol, pos in self.portfolio.positions.items()
        ]
        return pd.DataFrame(rows, columns=["symbol", "quantity", "price"])

    def exposure_summary(self, current_prices: Dict[str, float]) -> Optional[PortfolioExposureSummary]:
        positions_df = self.positions_frame(current_prices)
        if positions_df.empty:
            return None
        return summarize_portfolio_exposure(positions_df)

    def record_trade(
        self, symbol: str, side: str, price: float, quantity: float, pnl: Optional[float] = None
    ) -> None:
        # Tracked regardless of whether journal is configured - this is the
        # baseline sync_capital_with_exchange needs to tell trading-driven
        # capital changes apart from external ones, not just an audit record.
        notional = price * quantity
        if side == "buy":
            self._net_trading_cash_flow -= notional
        elif side == "sell":
            self._net_trading_cash_flow += notional
        if self.journal is not None:
            self.journal.record_trade(
                symbol=symbol, side=side, price=price, quantity=quantity, pnl=pnl, is_live=self.is_live
            )

    def reconcile_with_exchange(self, exchange: Any, symbols: List[str], stop_loss_pct: Optional[float] = None) -> List[str]:
        """Rebuild positions/capital from the REAL exchange balance on
        startup - PortfolioManager keeps positions/capital entirely in
        process memory with no persistence across restarts, so every
        restart (including the crash-resilient supervisor's own automatic
        ones) otherwise means the system "forgets" any real position it
        already holds. ExecutionAgent.execute() only guards a BUY when
        `symbol in self.portfolio.positions` - after an amnesia restart
        that guard sees an empty dict and happily buys AGAIN into a symbol
        already held for real, silently doubling exposure. Found via a real
        testnet account holding 2267 XRP while the in-memory portfolio
        tracked only the most recent 933.

        Cost basis is reconstructed from the journal's persisted trade
        history where it explains the real balance; any portion it can't
        explain (e.g. a testnet faucet grant, or a manual/out-of-band trade)
        is flagged loudly via a warning log and priced at the current
        market rate for PnL purposes - but that fair-value pricing is kept
        separate from the CAPITAL deduction below. Capital only decreases
        by money the journal proves this system actually spent; deducting
        the current market value of an unexplained balance from capital
        would double-count it (it already shows up in position value) and
        can drive capital deeply negative for something that was never
        actually paid for - found via a real testnet account where 19 of
        20 tracked symbols were entirely faucet-seeded balances, driving
        capital to -$90,253 despite total equity netting out consistently.

        If `stop_loss_pct` is given, every reconciled position that doesn't
        already have a resting protective stop order gets one backfilled -
        a position surviving a restart with no stop order (e.g. one opened
        before this feature existed, or whose stop was manually cancelled)
        would otherwise sit completely unprotected indefinitely.

        Reconciliation is deliberately SCOPED to `symbols` (the discovered
        trading universe, ~20-30 liquid USDT pairs) - it does not value or
        track every asset the exchange account happens to hold a nonzero
        balance of. On a real testnet account this scope boundary matters a
        lot in practice: Binance's testnet faucet seeds a small amount of
        essentially every listed asset (400+ found on a real audit), none
        of it meaningful trading capital. Silently ignoring everything
        outside `symbols` would misrepresent what "total portfolio value"
        actually means, though - see _log_untracked_balances, called below,
        which makes that scope boundary a logged, auditable fact instead of
        an invisible gap.

        Returns the symbols that ended this pass with a REAL, still-held
        position but no resting protective stop (only meaningful when
        stop_loss_pct is given) - a genuine capital-at-risk state (a naked
        position surviving a restart) the caller should alert a human
        about, not just log. Computed from the final position state, not
        tracked optimistically mid-loop, since _reverify_after_failed_stop
        can remove a position entirely if its balance turns out to have
        already vanished - that's not "unprotected", it's "not held".
        """
        try:
            balance = exchange.fetch_balance()
        except Exception:  # noqa: BLE001 - a failed reconciliation must not crash startup
            logger.exception("could not fetch exchange balance for portfolio reconciliation")
            return []

        self._log_untracked_balances(balance, symbols)

        for symbol in symbols:
            base_asset = symbol.split("/")[0]
            real_qty = float((balance.get(base_asset) or {}).get("total", 0.0) or 0.0)
            if real_qty <= 1e-8:
                continue  # exchange shows flat for this symbol - nothing to reconcile

            reconstructed = self._reconstruct_from_trade_history(symbol)
            journal_qty = reconstructed["quantity"] if reconstructed else 0.0

            if reconstructed and abs(journal_qty - real_qty) < max(real_qty * 0.01, 1e-6):
                entry_price = reconstructed["avg_price"]
                pnl_cost_basis = reconstructed["cost"]
                capital_actually_spent = reconstructed["cost"]
                explained_cost = reconstructed["cost"]
                unexplained_qty = 0.0
                logger.info(
                    "portfolio reconciled from trade history",
                    extra={"context": {"symbol": symbol, "quantity": real_qty, "avg_price": entry_price}},
                )
            else:
                unexplained_qty = real_qty - journal_qty
                try:
                    current_price = exchange.fetch_ticker(symbol).get("last")
                except Exception:  # noqa: BLE001
                    current_price = None
                if not current_price:
                    logger.error(
                        "cannot reconcile position - no price available for the unexplained balance",
                        extra={"context": {"symbol": symbol, "real_qty": real_qty, "journal_qty": journal_qty}},
                    )
                    continue
                explained_cost = reconstructed["cost"] if reconstructed else 0.0
                # PnL cost basis prices the unexplained portion at today's
                # fair value (so it doesn't show as an absurd 100% unrealized
                # gain from a $0 basis) - but capital only ever decreases by
                # the explained, journal-proven portion. The unexplained
                # portion was never actually paid for out of this system's
                # capital, so it must not reduce it.
                pnl_cost_basis = explained_cost + unexplained_qty * current_price
                capital_actually_spent = explained_cost
                entry_price = pnl_cost_basis / real_qty
                logger.warning(
                    "portfolio reconciliation found exchange balance the journal doesn't fully explain - "
                    "pricing the unexplained portion at current market rate for PnL, without deducting "
                    "capital that was never actually spent",
                    extra={
                        "context": {
                            "symbol": symbol,
                            "real_qty": real_qty,
                            "journal_explained_qty": journal_qty,
                            "unexplained_qty": unexplained_qty,
                        }
                    },
                )

            self.portfolio.positions[symbol] = {
                "price": entry_price,
                "quantity": real_qty,
                "cost": pnl_cost_basis,
                # Excluded from get_total_value/get_position_pnl (live
                # equity/PnL) so a faucet-seeded or otherwise unexplained
                # balance's current market value doesn't get counted as
                # strategy trading capital/performance - see
                # PortfolioManager._tracked_quantity. Still the FULL real
                # quantity everywhere else (selling, protective stops,
                # exposure risk), since the account is genuinely exposed to
                # its price risk regardless of who paid for it.
                "unexplained_quantity": unexplained_qty,
                # The explained sub-portion's OWN real cost basis, tracked
                # separately from the blended `cost` above - needed so
                # PortfolioManager.close_position can attribute realized
                # PnL/capital correctly on a sell instead of taking a flat
                # proportional slice of the blended total, which would
                # misattribute PnL whenever the explained and unexplained
                # sub-portions were priced differently (e.g. an explained
                # buy at $1 blended with an unexplained balance priced at
                # $2 at reconciliation time).
                "explained_cost": explained_cost,
                "stop_loss": None,
                "take_profit": None,
                "timestamp": pd.Timestamp.now(),
                # Needed by Orchestrator._retry_missing_protective_stop so a
                # position that survives a restart with a still-missing
                # stop (backfill below failed) gets retried every cycle
                # with the SAME intended distance, not just once here.
                "intended_stop_loss_pct": stop_loss_pct,
            }
            self.portfolio.capital -= capital_actually_spent

            if stop_loss_pct is not None:
                existing_stop_id = find_open_protective_stop(exchange, symbol)
                if existing_stop_id is not None:
                    self.portfolio.positions[symbol]["stop_order_id"] = existing_stop_id
                else:
                    new_stop_id = place_protective_stop(exchange, symbol, real_qty, entry_price, stop_loss_pct)
                    self.portfolio.positions[symbol]["stop_order_id"] = new_stop_id
                    if new_stop_id is None:
                        logger.error(
                            "reconciled position has NO protective stop and backfill failed - unprotected",
                            extra={"context": {"symbol": symbol, "quantity": real_qty}},
                        )
                        # A stop placement failure (e.g. InsufficientFunds)
                        # is meaningful evidence the balance snapshot taken
                        # at the top of this reconciliation pass may already
                        # be stale - testnet balances can genuinely change
                        # mid-pass (confirmed live: a real LINK/USDT
                        # reconciliation recorded real_qty=58 moments before
                        # the exchange reported the balance as fully gone).
                        # Re-verify against the exchange right now rather
                        # than leaving a position record built from data
                        # that's already proven wrong.
                        self._reverify_after_failed_stop(exchange, symbol, base_asset)

        if stop_loss_pct is None:
            return []
        return [
            symbol for symbol in symbols
            if symbol in self.portfolio.positions and self.portfolio.positions[symbol].get("stop_order_id") is None
        ]

    def sync_capital_with_exchange(
        self, exchange: Any, quote_currency: str = "USDT"
    ) -> Optional[CashFlowClassification]:
        """LIVE MODE ONLY - set tradable cash capital from the exchange's
        own real FREE balance, never from config's initial_capital.

        Returns a CashFlowClassification comparing the raw balance change
        since the last sync against the cash effect of this agent's OWN
        recorded trades in that window - the caller (Orchestrator) uses
        `is_external_flow` to alert and to rebase RiskAgent's drawdown peak,
        so a withdrawal/deposit/transfer to another product never registers
        as trading performance. None when the exchange call itself failed
        (nothing to classify) or on the very first sync (no prior capital
        to compare against).

        reconcile_with_exchange (above) already rebuilds real POSITIONS
        (crypto currently held) from the exchange, replacing whatever the
        in-memory PortfolioManager assumed - but nothing previously did the
        equivalent for CASH. self.portfolio.capital starts at
        initial_capital and is only ever adjusted downward as reconciled
        positions consume it - it never gets pulled UP or DOWN to match
        what the real account actually holds. On testnet that's fine on
        purpose: initial_capital is a deliberate, controlled fictional
        bankroll for evaluation, independent of testnet's messy
        faucet-seeded junk balances (see reconcile_with_exchange's own
        docstring on why those are scoped out). On a REAL live account
        there is no such thing as "starting paper capital" - a fresh live
        account with $0 would otherwise still report the full configured
        initial_capital (e.g. $1000), size real orders against that
        fictional number, and have every one rejected by the exchange for
        insufficient balance (tripping the circuit breaker after
        max_consecutive_errors) instead of correctly recognizing "no funds
        yet, wait". Call this AFTER reconcile_with_exchange, not before -
        order doesn't matter for correctness (this always overwrites
        capital with the real free balance regardless of what position
        reconciliation did to it in between), but doing it after keeps the
        cash step conceptually "layered on top of" the positions step.

        `free`, not `total`: total includes whatever's reserved in open
        orders (e.g. quote currency locked behind a resting limit buy),
        which is not available to spend on a NEW position right now.
        """
        try:
            balance = exchange.fetch_balance()
        except Exception:  # noqa: BLE001 - a failed sync must not crash startup; capital stays whatever it was
            logger.exception("could not fetch exchange balance for live capital sync - capital left unchanged")
            return None
        free_balance = float((balance.get(quote_currency) or {}).get("free", 0.0) or 0.0)
        previous_capital = self.portfolio.capital
        logger.info(
            "live capital synced from real exchange balance",
            extra={
                "context": {
                    "quote_currency": quote_currency,
                    "free_balance": free_balance,
                    "previous_capital": previous_capital,
                }
            },
        )

        classification: Optional[CashFlowClassification] = None
        if self._has_synced_before:
            trading_delta = self._net_trading_cash_flow - self._net_trading_cash_flow_at_last_sync
            classification = classify_balance_change(previous_capital, free_balance, trading_delta)
            if classification.is_external_flow:
                logger.warning(
                    "detected a balance change not explained by this bot's own trading - "
                    "likely a deposit, withdrawal, or transfer to another product",
                    extra={"context": {"unexplained_delta": classification.unexplained_delta}},
                )
        self._net_trading_cash_flow_at_last_sync = self._net_trading_cash_flow
        self._has_synced_before = True

        self.portfolio.capital = free_balance
        return classification

    def _reverify_after_failed_stop(self, exchange: Any, symbol: str, base_asset: str) -> None:
        try:
            fresh_balance = exchange.fetch_balance()
        except Exception:  # noqa: BLE001
            logger.exception(
                "could not re-verify balance after failed protective stop placement",
                extra={"context": {"symbol": symbol}},
            )
            return
        fresh_qty = float((fresh_balance.get(base_asset) or {}).get("total", 0.0) or 0.0)
        if fresh_qty <= 1e-8:
            logger.warning(
                "balance vanished between the reconciliation snapshot and stop placement - "
                "removing the now-stale position record",
                extra={"context": {"symbol": symbol}},
            )
            self.portfolio.positions.pop(symbol, None)
        else:
            logger.warning(
                "balance changed between the reconciliation snapshot and stop placement - "
                "position record may be stale",
                extra={"context": {"symbol": symbol, "fresh_qty": fresh_qty}},
            )

    @staticmethod
    def _find_untracked_assets(balance: Dict[str, Any], tracked_symbols: List[str]) -> List[str]:
        """Every asset with a real, non-zero total balance that isn't the
        base asset of any symbol in `tracked_symbols` - pure and directly
        testable, separated from the logging side-effect below."""
        tracked_base_assets = {symbol.split("/")[0] for symbol in tracked_symbols}
        totals = balance.get("total") or {}
        return sorted(
            asset for asset, qty in totals.items() if asset not in tracked_base_assets and qty and float(qty) > 1e-8
        )

    def _log_untracked_balances(self, balance: Dict[str, Any], tracked_symbols: List[str]) -> None:
        """Make the reconciliation scope boundary a logged, auditable fact:
        the account may hold real, nonzero balances for assets entirely
        outside the discovered trading universe. Deliberately NOT valued or
        risk-managed here (this system trades its discovered top-liquidity
        USDT pairs, not hundreds of untradeable dust tokens, and pricing
        every one of them would mean hundreds of extra API calls for no
        actionable benefit) - but silently ignoring them would let "total
        tracked equity" quietly be read as "total account value", which it
        is not. Costs nothing extra: reuses the balance dict already
        fetched for the main reconciliation loop below.

        Found via a real account audit: over 440 non-zero balances existed
        (Binance testnet's faucet seeds a small amount of essentially every
        listed asset), while the system's own discovered universe covered
        only ~20-30 of them.
        """
        untracked = self._find_untracked_assets(balance, tracked_symbols)
        if untracked:
            logger.warning(
                "account holds non-zero balances for assets OUTSIDE the tracked trading universe - "
                "these are NOT included in any tracked equity/PnL/risk figures",
                extra={
                    "context": {
                        "untracked_asset_count": len(untracked),
                        "tracked_asset_count": len({s.split('/')[0] for s in tracked_symbols}),
                        "sample": untracked[:15],
                    }
                },
            )

    def _reconstruct_from_trade_history(self, symbol: str) -> Optional[Dict[str, float]]:
        """Replay this symbol's persisted buy/sell trades chronologically to
        derive the currently-open position's quantity and weighted-average
        cost basis, if any - None if the history nets out flat."""
        if self.journal is None:
            return None
        trades = [t for t in self.journal.recent_trades(limit=5000) if t["symbol"] == symbol]
        trades.sort(key=lambda t: t["timestamp"])

        quantity = 0.0
        cost = 0.0
        for t in trades:
            if t["side"] == "buy":
                quantity += t["quantity"]
                cost += t["price"] * t["quantity"]
            elif t["side"] == "sell" and quantity > 1e-12:
                avg_price = cost / quantity
                sell_qty = min(t["quantity"], quantity)
                cost -= avg_price * sell_qty
                quantity -= sell_qty

        if quantity <= 1e-8:
            return None
        return {"quantity": quantity, "avg_price": cost / quantity, "cost": cost}
