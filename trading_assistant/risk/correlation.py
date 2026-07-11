"""Correlation-aware concentration risk.

A per-symbol dollar/weight cap alone (RiskAgent's existing
max_position_weight / max_gross_exposure_pct / max_net_exposure_pct) can't see that
20 "different" positions might really be one leveraged bet on the same
underlying market factor. Crypto altcoins are typically 70-90%+ correlated
to BTC, especially during risk-off moves - exactly the moments a risk
system exists to protect against are the ones where "diversification"
turns out to have been an illusion.

PortfolioRiskAggregator (trading_assistant/risk/advanced_metrics.py)
already computes CVaR/marginal-VaR machinery, but needs a pre-built weights
vector across a FIXED, known asset set - awkward for a live per-trade gate
evaluating one candidate against a dynamically changing, discovered symbol
universe. This module is purpose-built for that instead: a direct,
incremental "would approving this ONE trade push a correlated cluster's
combined exposure too high" check, using return history the orchestrator
already fetches every cycle (no extra API calls).
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd


def compute_returns(price_history: Dict[str, pd.Series], lookback_bars: int = 50) -> Dict[str, pd.Series]:
    """Convert each symbol's recent close-price history into pct-change
    returns, trimmed to a common lookback window. Symbols with fewer than 2
    resulting return observations are dropped - there's no correlation to
    compute from a single data point."""
    returns: Dict[str, pd.Series] = {}
    for symbol, closes in price_history.items():
        if closes is None or len(closes) < 2:
            continue
        r = closes.pct_change().dropna()
        if lookback_bars is not None:
            r = r.tail(lookback_bars)
        if len(r) >= 2:
            returns[symbol] = r
    return returns


def correlated_exposure_pct(
    candidate_symbol: str,
    candidate_notional: float,
    held_notional: Dict[str, float],
    returns: Dict[str, pd.Series],
    total_portfolio_value: float,
    correlation_threshold: float = 0.6,
    min_overlapping_bars: int = 5,
) -> float:
    """What fraction of total_portfolio_value would be concentrated in
    positions correlated (>= correlation_threshold) with candidate_symbol,
    INCLUDING the candidate's own proposed notional - not just
    candidate_symbol's isolated weight.

    A symbol pair with insufficient overlapping return history to compute a
    meaningful correlation (fewer than min_overlapping_bars aligned points)
    is conservatively treated as UNCORRELATED (contributes 0), not as
    dangerous - there's no evidence either way for a brand-new or
    thinly-tracked symbol, and failing open on a DATA gap is the right
    default; failing open on actual measured correlation would not be.
    """
    if total_portfolio_value <= 0:
        return 0.0

    correlated_notional = candidate_notional
    candidate_returns = returns.get(candidate_symbol)

    for symbol, notional in held_notional.items():
        if symbol == candidate_symbol:
            continue
        held_returns = returns.get(symbol)
        if candidate_returns is None or held_returns is None:
            continue
        aligned = pd.concat([candidate_returns, held_returns], axis=1, join="inner")
        if len(aligned) < min_overlapping_bars:
            continue
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if pd.notna(corr) and corr >= correlation_threshold:
            correlated_notional += notional

    return correlated_notional / total_portfolio_value
