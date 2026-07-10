"""Backtest-vs-live-paper correlation validation.

Every promotion decision in this system (LearningAgent, StrategyManager's
tournament) rests on one assumption: that walk-forward BACKTEST performance
is actually predictive of REAL forward performance. Nothing before this
module ever checked that assumption directly - the tournament could, in
principle, be consistently picking candidates whose backtest ranking has
no relationship (or even an inverse relationship) to how they actually
perform once ChampionChallengerAgent starts paper-trading them live. This
is exactly the "prove real edge" evidence this system's paper-trading
audit trail exists to eventually support.

The check: for every (symbol, candidate) pair with enough live paper
trades to say anything, compare its walk-forward BACKTEST Sharpe (from the
most recent tournament run) against its actual LIVE PAPER Sharpe (from
ChampionChallengerAgent's persisted equity curve). A meaningful positive
correlation across many such pairs is evidence the backtest methodology
transfers to live conditions. A correlation near zero is inconclusive
(not yet disqualifying - many pairs are needed before this signal
stabilizes). A clearly NEGATIVE correlation is a genuine red flag: it
would mean the backtest ranks candidates in roughly the opposite order of
how they actually perform live, which undermines the entire premise of
walk-forward-gated promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

# Deliberately not razor-thin: with a modest number of (symbol, candidate)
# pairs and inherently noisy Sharpe estimates, only a correlation clearly
# above/below zero is treated as a real signal rather than sampling noise.
POSITIVE_CORRELATION_THRESHOLD = 0.2
NEGATIVE_CORRELATION_THRESHOLD = -0.2


@dataclass
class BacktestVsLiveRecord:
    symbol: str
    candidate: str
    backtest_sharpe: float
    live_sharpe: float
    live_trades: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "candidate": self.candidate,
            "backtest_sharpe": self.backtest_sharpe,
            "live_sharpe": self.live_sharpe,
            "live_trades": self.live_trades,
        }


def backtest_sharpe_by_candidate(tournament_report: dict) -> Dict[str, float]:
    """Per-candidate backtest Sharpe from a persisted tournament_events
    report (StrategyManager.TournamentResult.to_dict()): a fold-count-
    weighted average across regimes that had any walk-forward folds, since
    live paper trading isn't regime-segmented the way the tournament's
    backtest is - there's no live equivalent of "this candidate's
    trending-down-only Sharpe" to compare against."""
    result: Dict[str, float] = {}
    for candidate in tournament_report.get("candidates", []):
        total_folds = 0
        weighted_sum = 0.0
        for perf in candidate.get("by_regime", {}).values():
            folds = perf.get("num_folds", 0)
            if folds:
                total_folds += folds
                weighted_sum += perf["median_sharpe"] * folds
        if total_folds:
            result[candidate["name"]] = weighted_sum / total_folds
    return result


def build_records(
    tournament_reports: Dict[str, dict],
    live_performance: Dict[str, Dict[str, dict]],
    min_live_trades: int = 5,
) -> List[BacktestVsLiveRecord]:
    """Pairs each symbol/candidate's latest backtest Sharpe with its live
    paper Sharpe. `live_performance` is symbol -> candidate ->
    {"sharpe_ratio": float, "num_trades": int}. A pair is only included
    once it has min_live_trades live paper trades - too few trades makes
    Sharpe noise, not signal, same reasoning as
    ChampionChallengerAgent.min_trades_to_compare."""
    records: List[BacktestVsLiveRecord] = []
    for symbol, report in tournament_reports.items():
        backtest_sharpes = backtest_sharpe_by_candidate(report)
        candidate_live = live_performance.get(symbol, {})
        for candidate, backtest_sharpe in backtest_sharpes.items():
            live = candidate_live.get(candidate)
            if live is None or live.get("num_trades", 0) < min_live_trades:
                continue
            records.append(
                BacktestVsLiveRecord(
                    symbol=symbol,
                    candidate=candidate,
                    backtest_sharpe=backtest_sharpe,
                    live_sharpe=live["sharpe_ratio"],
                    live_trades=live["num_trades"],
                )
            )
    return records


def average_backtest_sharpe(tournament_report: dict) -> Optional[float]:
    """Average walk-forward backtest Sharpe across EVERY candidate tested
    in the tournament (not just whichever one won) - an unbiased baseline
    to compare a champion's LIVE performance against.

    Comparing a champion's live Sharpe against its OWN backtest Sharpe is
    subject to survivorship/selection bias: the champion was picked as the
    best of N candidates on (out-of-sample, but still finite and noisy)
    walk-forward data, so even with zero real live decay, the best-of-N
    draw is expected to look better than the candidates' true average edge
    - "the champion beat the field" is baked into how it became champion,
    not necessarily evidence its edge is unusually large. Averaging across
    every tested candidate removes that selection effect and gives a fair
    baseline: is the champion still doing about as well as a typical
    candidate from this same tournament, or notably worse?

    None if no candidate had any qualifying walk-forward folds."""
    sharpes = list(backtest_sharpe_by_candidate(tournament_report).values())
    if not sharpes:
        return None
    return float(np.mean(sharpes))


@dataclass
class SurvivorshipBiasRecord:
    """One (symbol, regime)'s live champion, compared against the unbiased
    average of every candidate the tournament actually tested for that
    symbol - see average_backtest_sharpe's docstring for why this is a
    fairer baseline than the champion's own backtest Sharpe."""

    symbol: str
    regime: str
    champion: str
    champion_live_sharpe: float
    champion_live_trades: int
    avg_candidate_backtest_sharpe: float
    beats_baseline: bool

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime,
            "champion": self.champion,
            "champion_live_sharpe": self.champion_live_sharpe,
            "champion_live_trades": self.champion_live_trades,
            "avg_candidate_backtest_sharpe": self.avg_candidate_backtest_sharpe,
            "beats_baseline": self.beats_baseline,
        }


def build_survivorship_records(
    tournament_reports: Dict[str, dict],
    winners: Dict[str, Dict[str, Optional[str]]],
    live_performance: Dict[str, Dict[str, dict]],
    min_live_trades: int = 5,
) -> List[SurvivorshipBiasRecord]:
    """winners: symbol -> regime -> champion name (or None - skipped, "no
    champion" has nothing to compare). live_performance: same shape
    build_records already takes (symbol -> candidate -> {"sharpe_ratio",
    "num_trades"}) - the champion's own live paper performance, looked up
    by whichever candidate currently holds that regime."""
    records: List[SurvivorshipBiasRecord] = []
    for symbol, report in tournament_reports.items():
        avg_backtest = average_backtest_sharpe(report)
        if avg_backtest is None:
            continue
        for regime, champion in winners.get(symbol, {}).items():
            if champion is None:
                continue
            live = live_performance.get(symbol, {}).get(champion)
            if live is None or live.get("num_trades", 0) < min_live_trades:
                continue
            records.append(
                SurvivorshipBiasRecord(
                    symbol=symbol,
                    regime=regime,
                    champion=champion,
                    champion_live_sharpe=live["sharpe_ratio"],
                    champion_live_trades=live["num_trades"],
                    avg_candidate_backtest_sharpe=avg_backtest,
                    beats_baseline=live["sharpe_ratio"] >= avg_backtest,
                )
            )
    return records


def compute_correlation(records: List[BacktestVsLiveRecord]) -> Optional[float]:
    """Pearson correlation between backtest and live Sharpe across all
    qualifying records. None (not zero) if there aren't enough points to
    say anything, or either side has zero variance (correlation is
    undefined, not "no correlation") - "can't compute" must never be
    silently reported as "no relationship found"."""
    if len(records) < 3:
        return None
    backtest = np.array([r.backtest_sharpe for r in records])
    live = np.array([r.live_sharpe for r in records])
    if np.std(backtest) == 0 or np.std(live) == 0:
        return None
    return float(np.corrcoef(backtest, live)[0, 1])


def correlation_verdict(correlation: Optional[float]) -> str:
    """'insufficient_data' | 'positive' | 'weak_or_none' | 'negative'."""
    if correlation is None:
        return "insufficient_data"
    if correlation >= POSITIVE_CORRELATION_THRESHOLD:
        return "positive"
    if correlation <= NEGATIVE_CORRELATION_THRESHOLD:
        return "negative"
    return "weak_or_none"
