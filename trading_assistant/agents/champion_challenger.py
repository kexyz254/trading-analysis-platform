"""ChampionChallengerAgent: every cycle, every non-champion tournament
candidate trades in its own dedicated PAPER portfolio, fed the exact same
live bar the champion sees - building a continuous, forward-only track
record for every candidate, not just the one currently deployed.

This is deliberately the complementary half to StrategyManager's
walk-forward validation: the walk-forward number says "this looked good on
past data"; this live tracker says "this IS keeping pace with (or beating)
the champion right now, in real time." Both matter - a challenger that only
shines in one historical backtest window is not the same claim as one
that's been quietly outperforming the champion for weeks of live data it
was never fitted to.

Paper trades never touch a real (or even testnet) exchange - they're
recorded directly against an in-memory PortfolioManager per candidate.
Promotion (making a challenger the new champion) is NOT automatic here:
compare_to_champion() flags outperformance for review, the same caution
this system has applied to every other promotion decision tonight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.strategy_manager import StrategyManager
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.risk.backtest_sizing import risk_based_quantity
from trading_assistant.strategies.base import Signal

logger = logging.getLogger(__name__)


@dataclass
class ChallengerReport:
    symbol: str
    candidate_name: str
    total_value: float
    sharpe_ratio: float
    max_drawdown: float
    num_trades: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "candidate_name": self.candidate_name,
            "total_value": self.total_value,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "num_trades": self.num_trades,
        }


class ChampionChallengerAgent:
    def __init__(
        self,
        strategy_manager: StrategyManager,
        paper_capital: float = 10000.0,
        min_trades_to_compare: int = 5,
        journal: Optional[Journal] = None,
        commission_rate: float = 0.0,
        slippage_rate: float = 0.0,
        # Walk-forward sizing parity (trading_assistant.risk.backtest_sizing) -
        # see StrategyManager's identical parameter for why: paper-trading
        # every challenger at a flat ~95% of capital would make its
        # "keeping pace with the champion" track record reflect a risk
        # level nothing here would ever really be traded at. None keeps
        # the old flat sizing (opt-in, backward compatible).
        risk_per_trade: Optional[float] = None,
        position_stop_loss_pct: float = 0.01,
        max_position_size: float = 0.1,
        alert_manager: Optional[AlertManager] = None,
    ):
        self.strategy_manager = strategy_manager
        self.paper_capital = paper_capital
        self.min_trades_to_compare = min_trades_to_compare
        self.journal = journal
        self.risk_per_trade = risk_per_trade
        self.position_stop_loss_pct = position_stop_loss_pct
        self.max_position_size = max_position_size
        self.alert_manager = alert_manager
        # Champion-challenger WITH FRICTION: defaults to 0.0 (opt-in, same as
        # PortfolioManager itself) but should be set to match real trading
        # costs in production - otherwise a challenger's paper P&L looks
        # better than the champion's real P&L purely because it never pays
        # commission/slippage, which is exactly the comparison this
        # architecture exists to make honest.
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        # symbol -> candidate_name -> that candidate's dedicated paper portfolio
        self._portfolios: Dict[str, Dict[str, PortfolioManager]] = {}

    def process_cycle(
        self, symbol: str, regime: str, features: pd.DataFrame, current_price: float, exclude_champion: bool = True,
        sitting_out: bool = False,
    ) -> None:
        """Paper-trade tournament candidates against this live bar.

        `exclude_champion` defaults to True (the original design): when the
        tournament is actually driving real execution (drive_live_trading),
        the champion already trades for real elsewhere and must never be
        double-counted here. When the tournament is still observation-only
        (drive_live_trading=False - the common case while building up a
        stability track record), nothing else is tracking the "would-be
        champion" candidate for real, so pass exclude_champion=False to paper
        trade it too - otherwise its bankroll would be the one candidate
        missing from the leaderboard.

        `sitting_out` mirrors the real decision path's own market_condition
        sit-out (panic/illiquid - see Orchestrator._process_symbol): when
        True, no candidate may open a NEW position on this bar, matching
        the protection the real champion already gets. Existing positions
        can still be closed (a challenger cutting a losing position during
        a panic bar isn't the fairness problem - accumulating NEW risk
        during exactly the conditions the real system is designed to
        avoid is). Without this, challengers kept trading through bars the
        champion sat out, so a champion-vs-challenger comparison was
        implicitly comparing different risk exposure, not just different
        strategies - the module's own "same live bar, same friction, fair
        comparison" promise didn't fully hold."""
        champion_name = self.strategy_manager.winner_for(symbol, regime) if exclude_champion else None
        deployed = self.strategy_manager.live_instances.get(symbol, {})

        for candidate_name in deployed:
            if exclude_champion and candidate_name == champion_name:
                continue
            self._paper_trade(symbol, candidate_name, features, current_price, sitting_out=sitting_out)

    def _paper_trade(
        self, symbol: str, candidate_name: str, features: pd.DataFrame, current_price: float,
        sitting_out: bool = False,
    ) -> None:
        if current_price <= 0:
            return

        portfolio = self._get_or_create_portfolio(symbol, candidate_name)
        signal = self.strategy_manager.live_signal(symbol, candidate_name, features)

        action = "no_action"
        pnl = None

        if signal == Signal.BUY and sitting_out:
            # Still computed and journaled below (what the strategy WOULD
            # have done, for audit/transparency) - just not acted on. See
            # process_cycle's docstring.
            action = "sit_out"
        elif signal == Signal.BUY and symbol not in portfolio.positions:
            if self.risk_per_trade is not None:
                quantity = risk_based_quantity(
                    portfolio.capital, current_price, self.position_stop_loss_pct,
                    risk_per_trade=self.risk_per_trade, max_position_size=self.max_position_size,
                )
            else:
                quantity = (portfolio.capital * 0.95) / current_price
            if quantity > 0 and portfolio.open_position(symbol, current_price, quantity):
                action = "opened_position"
        elif signal == Signal.SELL and symbol in portfolio.positions:
            pnl = portfolio.close_position(symbol, current_price)
            action = "closed_position"

        # Mark-to-market every cycle regardless of action, same reasoning as
        # PortfolioAgent.mark_to_market: an equity curve that only updates on
        # trades is blind to unrealized drift between them.
        total_value = portfolio.get_total_value({symbol: current_price})
        portfolio.append_equity_point(total_value)

        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal=signal.name,
                confidence=None,
                risk_approved=True,
                risk_reason=None,
                action=action,
                price=current_price,
                quantity=0.0,
                details={"paper": True, "candidate": candidate_name, "pnl": pnl},
            )
            # Persisted separately from the main equity_snapshots table (which
            # is the real/testnet portfolio) - a separate process like the
            # dashboard has no access to this in-memory PortfolioManager
            # otherwise, so without this, "watch the paper bankroll change in
            # real time" would be impossible outside this running process.
            self.journal.record_paper_equity_snapshot(
                symbol=symbol,
                candidate=candidate_name,
                capital=portfolio.capital,
                total_equity=total_value,
                num_trades=len(portfolio.trade_history),
            )

    def _get_or_create_portfolio(self, symbol: str, candidate_name: str) -> PortfolioManager:
        symbol_portfolios = self._portfolios.setdefault(symbol, {})
        if candidate_name not in symbol_portfolios:
            portfolio = PortfolioManager(
                initial_capital=self.paper_capital,
                commission_rate=self.commission_rate,
                slippage_rate=self.slippage_rate,
            )
            self._resume_from_journal(portfolio, symbol, candidate_name)
            symbol_portfolios[candidate_name] = portfolio
        return symbol_portfolios[candidate_name]

    def _resume_from_journal(self, portfolio: PortfolioManager, symbol: str, candidate_name: str) -> None:
        """Reconstruct this paper candidate's equity-curve/capital/trade-
        count continuity from its own persisted history.

        Without this, every restart (including the crash-resilient
        supervisor's own automatic ones) would silently reset paper
        trading back to a flat paper_capital start - corrupting any
        equity curve spanning the restart with a fake "reset"
        discontinuity that would invalidate any Sharpe/drawdown/return
        computed across the full history. This is the paper-trading
        analog of PortfolioAgent.reconcile_with_exchange for the real
        portfolio: same underlying problem (in-memory-only state, no
        persistence across restarts), same fix (rebuild continuity from
        the persisted, ground-truth history) - the whole point of
        tracking every candidate's paper performance is to eventually
        have real evidence of edge, and evidence riddled with silent
        fake resets every time this process happens to restart is not
        evidence of anything.

        An open paper position at the moment of a restart is NOT
        reconstructed - there's no persisted record of its exact
        quantity/entry price to replay (unlike the real portfolio, which
        can be reconciled against the actual exchange balance). It is
        conservatively treated as if it had been marked to market and
        the proceeds kept as cash, the most defensible approximation
        available from what's actually persisted.
        """
        if self.journal is None:
            return
        snapshots = self.journal.recent_paper_equity_snapshots(symbol=symbol, candidate=candidate_name)
        if not snapshots:
            return
        portfolio.equity_curve = [portfolio.initial_capital] + [s["total_equity"] for s in snapshots]
        portfolio.capital = snapshots[-1]["capital"]
        # trade_history's CONTENTS are never read anywhere in this class -
        # only its length (num_trades gating in compare_to_champion) - so a
        # placeholder of the right length is a safe, honest way to resume
        # the count without fabricating fake trade details.
        resumed_trade_count = snapshots[-1]["num_trades"]
        portfolio.trade_history = [{"resumed_from_journal": True}] * resumed_trade_count

    def report(self, symbol: str) -> List[ChallengerReport]:
        reports = []
        for candidate_name, portfolio in self._portfolios.get(symbol, {}).items():
            reports.append(
                ChallengerReport(
                    symbol=symbol,
                    candidate_name=candidate_name,
                    total_value=portfolio.equity_curve[-1] if portfolio.equity_curve else portfolio.initial_capital,
                    sharpe_ratio=portfolio.get_sharpe_ratio(),
                    max_drawdown=portfolio.get_max_drawdown(),
                    num_trades=len(portfolio.trade_history),
                )
            )
        return reports

    def compare_to_champion(self, symbol: str, champion_portfolio: PortfolioManager) -> List[ChallengerReport]:
        """Flag challengers whose live paper Sharpe currently exceeds the
        champion's real (or testnet) live Sharpe, with enough trades to be
        a meaningful comparison rather than noise. This is a signal for
        human/operator review, not an automatic promotion - the same
        walk-forward + stability rigor this system applies everywhere else
        should gate any actual champion change."""
        champion_sharpe = champion_portfolio.get_sharpe_ratio()
        outperforming = [
            rep
            for rep in self.report(symbol)
            if rep.num_trades >= self.min_trades_to_compare and rep.sharpe_ratio > champion_sharpe
        ]
        if outperforming:
            logger.info(
                "challenger(s) currently outperforming the champion",
                extra={
                    "context": {
                        "symbol": symbol,
                        "champion_sharpe": champion_sharpe,
                        "challengers": [rep.to_dict() for rep in outperforming],
                    }
                },
            )
            if self.alert_manager is not None:
                names = ", ".join(rep.candidate_name for rep in outperforming)
                self.alert_manager.raise_alert(
                    "challenger_outperforming",
                    "info",
                    f"{symbol}: challenger(s) {names} currently beat the champion's live Sharpe "
                    f"({champion_sharpe:.2f}) - candidate(s) worth reviewing for promotion.",
                    context={"symbol": symbol, "champion_sharpe": champion_sharpe},
                    dedupe_key=f"challenger_outperforming:{symbol}",
                )
        return outperforming

    def underperforming_candidates(
        self, symbol: str, champion_portfolio: PortfolioManager
    ) -> List[ChallengerReport]:
        """The symmetric counterpart to compare_to_champion: flags
        challengers whose live paper Sharpe is currently WORSE than the
        champion's, with enough trades to be a meaningful comparison rather
        than noise. Previously there was no visibility into which
        candidates were chronically underperforming at all - a tournament
        keeps re-evaluating every registered candidate forever with no
        signal that some of them are dead weight worth an operator's
        attention. Like compare_to_champion, this only flags for human
        review; it never removes a candidate from the tournament itself -
        retirement is a deliberate decision, not an automatic one."""
        champion_sharpe = champion_portfolio.get_sharpe_ratio()
        underperforming = [
            rep
            for rep in self.report(symbol)
            if rep.num_trades >= self.min_trades_to_compare and rep.sharpe_ratio < champion_sharpe
        ]
        if underperforming:
            logger.info(
                "challenger(s) currently underperforming the champion",
                extra={
                    "context": {
                        "symbol": symbol,
                        "champion_sharpe": champion_sharpe,
                        "challengers": [rep.to_dict() for rep in underperforming],
                    }
                },
            )
            if self.alert_manager is not None:
                names = ", ".join(rep.candidate_name for rep in underperforming)
                self.alert_manager.raise_alert(
                    "challenger_underperforming",
                    "info",
                    f"{symbol}: challenger(s) {names} currently trail the champion's live Sharpe "
                    f"({champion_sharpe:.2f}) - candidate(s) worth reviewing for retirement.",
                    context={"symbol": symbol, "champion_sharpe": champion_sharpe},
                    dedupe_key=f"challenger_underperforming:{symbol}",
                )
        return underperforming
