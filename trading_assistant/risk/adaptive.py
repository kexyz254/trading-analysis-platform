"""Drawdown-based adaptive risk scaling.

A fixed risk_per_trade means the system risks the same dollar fraction on
the first trade after a fresh equity peak and on the fiftieth trade during
a 15% drawdown - exactly backwards from how professional systematic desks
manage risk, where a losing streak earns a SMALLER bet, not the same one,
until performance actually recovers. `RiskManager.calculate_kelly_fraction`
already existed in this codebase for a similar purpose but was never wired
into anything live; a Kelly-based approach needs accurate win-rate/payoff
estimates to be safe (an overestimate can size dangerously large), so a
simpler, more conservative, and easier-to-reason-about drawdown ladder is
used here instead: risk shrinks in discrete steps as drawdown from peak
equity deepens, floors at zero (halt opening new positions) past the
deepest tier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DrawdownTier:
    drawdown_pct: float  # e.g. 0.05 = 5% drawdown from peak equity
    risk_scale: float  # multiplier applied to risk_per_trade, e.g. 0.5 = half size


# Deliberately conservative, round numbers rather than backtested-and-
# overfit thresholds: this is a safety ladder, not an alpha signal.
DEFAULT_DRAWDOWN_TIERS: List[DrawdownTier] = [
    DrawdownTier(0.05, 0.75),
    DrawdownTier(0.10, 0.50),
    DrawdownTier(0.15, 0.25),
    DrawdownTier(0.20, 0.0),  # 20%+ drawdown from peak: stop opening new positions entirely
]


def compute_drawdown_pct(peak_equity: float, current_equity: float) -> float:
    """Fraction below peak equity current_equity currently sits at, floored
    at 0 (a new peak is 0% drawdown, never negative)."""
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity)


def compute_risk_scale(current_drawdown_pct: float, tiers: List[DrawdownTier] = DEFAULT_DRAWDOWN_TIERS) -> float:
    """The risk multiplier for the DEEPEST tier whose threshold has been
    breached by current_drawdown_pct, or 1.0 (full size) if none have -
    tiers are applied as a ladder (breaching a deeper tier implies every
    shallower one), not summed or averaged."""
    if current_drawdown_pct <= 0:
        return 1.0
    scale = 1.0
    for tier in sorted(tiers, key=lambda t: t.drawdown_pct):
        if current_drawdown_pct >= tier.drawdown_pct:
            scale = tier.risk_scale
    return scale
