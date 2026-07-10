import pytest

from trading_assistant.risk.execution_quality import spread_pct, spread_too_wide


def test_spread_pct_computes_fraction_of_mid_price():
    # bid=100, ask=101 -> spread=1, mid=100.5 -> 1/100.5
    assert spread_pct(100.0, 101.0) == pytest.approx(1.0 / 100.5)


def test_spread_pct_zero_when_bid_equals_ask():
    assert spread_pct(100.0, 100.0) == 0.0


def test_spread_pct_none_when_bid_missing():
    assert spread_pct(None, 101.0) is None


def test_spread_pct_none_when_ask_missing():
    assert spread_pct(100.0, None) is None


def test_spread_pct_none_when_non_positive():
    assert spread_pct(0.0, 101.0) is None
    assert spread_pct(100.0, 0.0) is None
    assert spread_pct(-1.0, 101.0) is None


def test_spread_pct_none_when_crossed_or_inverted():
    """A corrupt/stale quote where ask < bid must not produce a nonsensical
    negative spread that could accidentally read as 'tight'."""
    assert spread_pct(101.0, 100.0) is None


def test_spread_too_wide_true_when_spread_exceeds_threshold():
    # spread ~1% (100 -> 101), threshold 0.5% -> too wide
    assert spread_too_wide(100.0, 101.0, max_spread_pct=0.005) is True


def test_spread_too_wide_false_when_spread_within_threshold():
    assert spread_too_wide(100.0, 100.1, max_spread_pct=0.01) is False


def test_spread_too_wide_exact_threshold_is_not_too_wide():
    pct = spread_pct(100.0, 101.0)
    assert spread_too_wide(100.0, 101.0, max_spread_pct=pct) is False  # equal, not exceeding


def test_spread_too_wide_false_when_quote_unusable():
    """A missing/corrupt quote is a data gap, not evidence of an actually
    illiquid market - must not block the trade the same way a genuinely
    observed wide spread would."""
    assert spread_too_wide(None, 101.0, max_spread_pct=0.01) is False
    assert spread_too_wide(100.0, None, max_spread_pct=0.01) is False
    assert spread_too_wide(101.0, 100.0, max_spread_pct=0.01) is False
