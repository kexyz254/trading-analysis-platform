import numpy as np
import pandas as pd
import pytest

from trading_assistant.risk.correlation import compute_returns, correlated_exposure_pct


def _closes(values):
    return pd.Series(values, dtype=float)


def test_compute_returns_converts_closes_to_pct_change_and_trims_to_lookback():
    closes = _closes([100, 101, 102, 103, 104, 105])
    result = compute_returns({"BTC/USDT": closes}, lookback_bars=3)

    assert "BTC/USDT" in result
    assert len(result["BTC/USDT"]) == 3  # trimmed to the last 3 return observations
    assert result["BTC/USDT"].iloc[-1] == pytest.approx((105 - 104) / 104)


def test_compute_returns_drops_symbols_with_insufficient_history():
    result = compute_returns({"BTC/USDT": _closes([100]), "ETH/USDT": None}, lookback_bars=50)
    assert result == {}


def test_correlated_exposure_includes_held_positions_moving_in_lockstep():
    """Two symbols whose prices move in perfect lockstep must be treated as
    ONE combined exposure, not two independent 20% bets."""
    n = 30
    base = pd.Series(np.linspace(100, 130, n))
    btc_closes = base
    eth_closes = base * 20  # scaled but perfectly correlated (same relative moves)
    returns = compute_returns({"BTC/USDT": btc_closes, "ETH/USDT": eth_closes})

    pct = correlated_exposure_pct(
        candidate_symbol="ETH/USDT",
        candidate_notional=2000.0,
        held_notional={"BTC/USDT": 2000.0},
        returns=returns,
        total_portfolio_value=10000.0,
        correlation_threshold=0.6,
    )

    # candidate (2000) + correlated BTC holding (2000) = 4000 / 10000 = 0.4
    assert pct == pytest.approx(0.4)


def test_correlated_exposure_excludes_held_positions_moving_independently():
    n = 30
    rng = np.random.default_rng(42)
    candidate_closes = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    unrelated_closes = pd.Series(50 + np.cumsum(rng.normal(0, 1, n) * -1))  # different random walk
    returns = compute_returns({"NEW/USDT": candidate_closes, "OLD/USDT": unrelated_closes})

    pct = correlated_exposure_pct(
        candidate_symbol="NEW/USDT",
        candidate_notional=1000.0,
        held_notional={"OLD/USDT": 5000.0},
        returns=returns,
        total_portfolio_value=10000.0,
        correlation_threshold=0.9,  # deliberately high bar an unrelated walk won't clear
    )

    # only the candidate's own notional counts - OLD/USDT's correlation is far below the threshold
    assert pct == pytest.approx(0.1)


def test_correlated_exposure_treats_missing_history_as_uncorrelated_not_dangerous():
    """A brand-new/thinly-tracked symbol with no return history yet must not
    be assumed correlated purely from absence of data."""
    returns = compute_returns({"CANDIDATE/USDT": _closes([100, 101, 102])})  # HELD/USDT has no entry at all

    pct = correlated_exposure_pct(
        candidate_symbol="CANDIDATE/USDT",
        candidate_notional=1000.0,
        held_notional={"HELD/USDT": 9000.0},
        returns=returns,
        total_portfolio_value=10000.0,
    )

    assert pct == pytest.approx(0.1)  # HELD/USDT's 9000 was NOT added in


def test_correlated_exposure_ignores_pairs_with_too_few_overlapping_bars():
    returns = {
        "A/USDT": pd.Series([0.01, 0.02]),  # only 2 points - below default min_overlapping_bars=5
        "B/USDT": pd.Series([0.01, 0.02]),
    }

    pct = correlated_exposure_pct(
        candidate_symbol="A/USDT",
        candidate_notional=1000.0,
        held_notional={"B/USDT": 9000.0},
        returns=returns,
        total_portfolio_value=10000.0,
        correlation_threshold=0.5,
    )

    assert pct == pytest.approx(0.1)


def test_correlated_exposure_returns_zero_with_no_portfolio_value():
    pct = correlated_exposure_pct("A/USDT", 1000.0, {}, {}, total_portfolio_value=0.0)
    assert pct == 0.0


def test_correlated_exposure_candidate_alone_when_portfolio_empty():
    pct = correlated_exposure_pct("A/USDT", 1000.0, {}, {}, total_portfolio_value=10000.0)
    assert pct == pytest.approx(0.1)
