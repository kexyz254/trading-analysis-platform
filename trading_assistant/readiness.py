"""Go-live readiness scorecard: turns "are we ready to trust this with a
live decision" into a computed, auditable answer instead of a recurring
judgment call made by re-reading scattered numbers each time it's asked.

Every function here is a pure function of data already persisted in the
journal - nothing here depends on a live, in-process StrategyManager or
PortfolioManager, so a separate process (the dashboard) can compute the
identical report a human would get by inspecting the running system directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_assistant.config import ReadinessConfig
from trading_assistant.stability import compute_stability_score
from trading_assistant.performance_metrics import (
    performance_summary_from_trade_pnls,
    sharpe_ratio_from_equity_curve,
)
from trading_assistant.validation.backtest_correlation import (
    BacktestVsLiveRecord,
    SurvivorshipBiasRecord,
    compute_correlation,
    correlation_verdict,
)


@dataclass
class ReadinessCriterion:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class ReadinessReport:
    overall_ready: bool
    criteria: List[ReadinessCriterion] = field(default_factory=list)
    # Non-blocking: NEVER included in overall_ready's AND-gate, unlike
    # criteria above. A champion whose live paper Sharpe falls short of
    # the unbiased all-candidate backtest average (see
    # assess_survivorship_bias_warnings) is worth watching, but it's one
    # noisy signal - conflating it with a hard promotion/demotion gate
    # would be over-reacting to exactly the kind of small-sample noise
    # this system already goes out of its way to shrink elsewhere (see
    # PortfolioManager.wilson_lower_bound). Revisit after observing this
    # for a real stretch of time.
    warnings: List[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_ready": self.overall_ready,
            "criteria": [c.to_dict() for c in self.criteria],
            "warnings": self.warnings,
            "generated_at": self.generated_at,
        }


def assess_tournament_stability(
    winner_histories: Dict[str, Dict[str, List[Optional[str]]]], config: ReadinessConfig
) -> ReadinessCriterion:
    """Only symbol/regime combinations that have actually accumulated
    min_tournament_runs re-runs are judged - a regime that rarely occurs
    (e.g. "ranging" on a trending market) shouldn't silently drag down or
    silently pass the scorecard just because it has almost no data."""
    qualifying: List[tuple] = []
    for symbol, by_regime in winner_histories.items():
        for regime, history in by_regime.items():
            if len(history) >= config.min_tournament_runs:
                score = compute_stability_score(history)
                qualifying.append((symbol, regime, len(history), score))

    if not qualifying:
        return ReadinessCriterion(
            name="Tournament stability",
            passed=False,
            detail=f"No symbol/regime has reached {config.min_tournament_runs} tournament runs yet.",
        )

    unstable = [(s, r, n, sc) for s, r, n, sc in qualifying if sc is None or sc < config.min_stability_score]
    passed = len(unstable) == 0
    if passed:
        detail = (
            f"All {len(qualifying)} symbol/regime combination(s) with >={config.min_tournament_runs} runs "
            f"meet the >={config.min_stability_score:.0%} stability bar."
        )
    else:
        worst = sorted(unstable, key=lambda item: (item[3] if item[3] is not None else -1))[:3]
        examples = "; ".join(f"{s}/{r} = {sc:.2f}" if sc is not None else f"{s}/{r} = n/a" for s, r, _, sc in worst)
        detail = f"{len(unstable)} of {len(qualifying)} qualifying combination(s) below the stability bar: {examples}"
    return ReadinessCriterion(name="Tournament stability", passed=passed, detail=detail)


def assess_real_trading_performance(
    equity_curve: List[float], trade_pnls: List[float], config: ReadinessConfig
) -> ReadinessCriterion:
    num_trades = len(trade_pnls)
    if num_trades < config.min_real_trades:
        return ReadinessCriterion(
            name="Real trading performance",
            passed=False,
            detail=f"Only {num_trades} real trade(s) completed; need >={config.min_real_trades} before Sharpe means anything.",
        )
    sharpe = float(sharpe_ratio_from_equity_curve(equity_curve))
    summary = performance_summary_from_trade_pnls(trade_pnls)
    passed = bool(sharpe >= config.min_real_sharpe)
    detail = (
        f"{num_trades} real trades, Sharpe={sharpe:.2f} (need >={config.min_real_sharpe}), "
        f"win rate={summary.get('win_rate', 0):.0%}"
    )
    return ReadinessCriterion(name="Real trading performance", passed=passed, detail=detail)


def assess_elapsed_time(first_timestamp: Optional[str], config: ReadinessConfig, now: Optional[datetime] = None) -> ReadinessCriterion:
    now = now or datetime.now(timezone.utc)
    if first_timestamp is None:
        return ReadinessCriterion(
            name="Elapsed operating time", passed=False, detail="No history recorded yet."
        )
    first = datetime.fromisoformat(first_timestamp)
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    elapsed_days = (now - first).total_seconds() / 86400
    passed = elapsed_days >= config.min_elapsed_days
    detail = f"{elapsed_days:.1f} day(s) of continuous operation (need >={config.min_elapsed_days})"
    return ReadinessCriterion(name="Elapsed operating time", passed=passed, detail=detail)


def assess_circuit_breaker(latest_event: Optional[Dict[str, Any]]) -> ReadinessCriterion:
    tripped = bool(latest_event and latest_event.get("tripped"))
    detail = "Circuit breaker is currently TRIPPED." if tripped else "Circuit breaker has never tripped, or is reset."
    return ReadinessCriterion(name="Circuit breaker", passed=not tripped, detail=detail)


def assess_backtest_live_correlation(
    records: List[BacktestVsLiveRecord], config: ReadinessConfig
) -> ReadinessCriterion:
    """Does walk-forward BACKTEST performance actually predict LIVE paper
    performance? Every promotion decision in this system rests on that
    assumption - this is the one criterion that checks it directly rather
    than taking it on faith. Only a clearly NEGATIVE correlation fails this
    criterion (see backtest_correlation.py's threshold reasoning): a weak
    or inconclusive correlation is expected early on with few qualifying
    (symbol, candidate) pairs and isn't itself disqualifying, but a
    negative correlation - backtest ranking candidates in roughly the
    OPPOSITE order of how they perform live - is a genuine red flag."""
    if len(records) < config.min_records_for_correlation:
        return ReadinessCriterion(
            name="Backtest-vs-live correlation",
            passed=False,
            detail=(
                f"Only {len(records)} symbol/candidate pair(s) with enough live paper trades so far "
                f"(need >={config.min_records_for_correlation}) - not enough evidence yet to check whether "
                f"the walk-forward backtest predicts live performance."
            ),
        )
    correlation = compute_correlation(records)
    verdict = correlation_verdict(correlation)
    passed = verdict != "negative"
    correlation_str = f"{correlation:.2f}" if correlation is not None else "n/a"
    detail = (
        f"Across {len(records)} symbol/candidate pair(s), backtest-vs-live Sharpe correlation = "
        f"{correlation_str} ({verdict})."
    )
    return ReadinessCriterion(name="Backtest-vs-live correlation", passed=passed, detail=detail)


def assess_survivorship_bias_warnings(records: List[SurvivorshipBiasRecord]) -> List[str]:
    """One human-readable warning per (symbol, regime) champion whose live
    paper Sharpe falls short of the unbiased average backtest Sharpe
    across every candidate the tournament actually tested - see
    average_backtest_sharpe's docstring for why that average, not the
    champion's own (survivorship-biased) backtest number, is the fairer
    bar. Deliberately returns plain strings, not a new criterion: this is
    surfaced via ReadinessReport.warnings, which never gates
    overall_ready - see that field's docstring."""
    warnings = []
    for record in records:
        if not record.beats_baseline:
            warnings.append(
                f"{record.symbol} {record.regime} champion '{record.champion}': live paper Sharpe "
                f"{record.champion_live_sharpe:.2f} (over {record.champion_live_trades} trades) is below "
                f"{record.avg_candidate_backtest_sharpe:.2f}, the unbiased average backtest Sharpe across "
                f"every candidate the tournament tested (not just the winner) - could be genuine live decay, "
                f"or just the expected regression from being selected as the best of several noisy estimates. "
                f"Not yet enough evidence to say which; worth tracking over time."
            )
    return warnings


def build_readiness_report(
    *,
    winner_histories: Dict[str, Dict[str, List[Optional[str]]]],
    real_equity_curve: List[float],
    real_trade_pnls: List[float],
    first_timestamp: Optional[str],
    circuit_breaker_event: Optional[Dict[str, Any]],
    config: ReadinessConfig,
    backtest_live_records: Optional[List[BacktestVsLiveRecord]] = None,
    survivorship_records: Optional[List[SurvivorshipBiasRecord]] = None,
    now: Optional[datetime] = None,
) -> ReadinessReport:
    criteria = [
        assess_tournament_stability(winner_histories, config),
        assess_real_trading_performance(real_equity_curve, real_trade_pnls, config),
        assess_elapsed_time(first_timestamp, config, now=now),
        assess_circuit_breaker(circuit_breaker_event),
        assess_backtest_live_correlation(backtest_live_records or [], config),
    ]
    now = now or datetime.now(timezone.utc)
    return ReadinessReport(
        overall_ready=all(c.passed for c in criteria),
        criteria=criteria,
        warnings=assess_survivorship_bias_warnings(survivorship_records or []),
        generated_at=now.isoformat(),
    )
