import pytest

from trading_assistant.risk.adaptive import DrawdownTier, compute_drawdown_pct, compute_risk_scale

CUSTOM_TIERS = [DrawdownTier(0.05, 0.75), DrawdownTier(0.10, 0.5), DrawdownTier(0.20, 0.0)]


def test_compute_drawdown_pct_at_new_peak_is_zero():
    assert compute_drawdown_pct(peak_equity=10000.0, current_equity=10500.0) == 0.0


def test_compute_drawdown_pct_below_peak():
    assert compute_drawdown_pct(peak_equity=10000.0, current_equity=9000.0) == pytest.approx(0.10)


def test_compute_drawdown_pct_handles_zero_peak_without_dividing_by_zero():
    assert compute_drawdown_pct(peak_equity=0.0, current_equity=0.0) == 0.0


def test_compute_risk_scale_full_size_when_no_drawdown():
    assert compute_risk_scale(0.0, CUSTOM_TIERS) == 1.0


def test_compute_risk_scale_full_size_below_first_tier():
    assert compute_risk_scale(0.03, CUSTOM_TIERS) == 1.0  # below the 5% tier


def test_compute_risk_scale_picks_the_deepest_breached_tier():
    assert compute_risk_scale(0.06, CUSTOM_TIERS) == 0.75  # just past 5%, not yet 10%
    assert compute_risk_scale(0.12, CUSTOM_TIERS) == 0.5  # just past 10%, not yet 20%
    assert compute_risk_scale(0.25, CUSTOM_TIERS) == 0.0  # past the deepest tier - halt


def test_compute_risk_scale_exact_threshold_is_inclusive():
    assert compute_risk_scale(0.05, CUSTOM_TIERS) == 0.75
    assert compute_risk_scale(0.20, CUSTOM_TIERS) == 0.0


def test_compute_risk_scale_unsorted_tiers_still_pick_the_deepest():
    reversed_tiers = list(reversed(CUSTOM_TIERS))
    assert compute_risk_scale(0.12, reversed_tiers) == 0.5


def test_default_tiers_are_a_monotonically_decreasing_ladder():
    from trading_assistant.risk.adaptive import DEFAULT_DRAWDOWN_TIERS

    sorted_tiers = sorted(DEFAULT_DRAWDOWN_TIERS, key=lambda t: t.drawdown_pct)
    scales = [t.risk_scale for t in sorted_tiers]
    assert scales == sorted(scales, reverse=True)  # deeper drawdown never means MORE risk
    assert scales[-1] == 0.0  # the deepest tier halts new positions entirely
