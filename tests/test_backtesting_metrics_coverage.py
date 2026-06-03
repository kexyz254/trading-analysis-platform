import math

import pandas as pd

from trading_assistant.backtesting import metrics


def test_calculate_total_return_from_equity_curve():
    equity_curve = pd.Series([100.0, 110.0, 121.0])

    result = metrics.calculate_total_return(equity_curve)

    assert math.isclose(result, 0.21, rel_tol=1e-9)


def test_calculate_annualized_return_is_finite_for_growth_curve():
    equity_curve = pd.Series([100.0, 102.0, 104.0, 106.0])

    result = metrics.calculate_annualized_return(equity_curve, periods_per_year=252)

    assert math.isfinite(result)
    assert result > 0


def test_calculate_max_drawdown_detects_peak_to_trough_move():
    equity_curve = pd.Series([100.0, 120.0, 90.0, 110.0])

    result = metrics.calculate_max_drawdown(equity_curve)

    assert math.isclose(abs(result), 0.25, rel_tol=1e-9)


def test_calculate_sharpe_ratio_handles_non_constant_returns():
    returns = pd.Series([0.02, -0.01, 0.03, 0.01, -0.005])

    result = metrics.calculate_sharpe_ratio(
        returns,
        risk_free_rate=0.0,
        periods_per_year=252,
    )

    assert math.isfinite(result)


def test_calculate_sortino_ratio_handles_downside_returns():
    returns = pd.Series([0.02, -0.03, 0.01, -0.01, 0.04])

    result = metrics.calculate_sortino_ratio(
        returns,
        risk_free_rate=0.0,
        periods_per_year=252,
    )

    assert math.isfinite(result)


def test_calculate_calmar_ratio_uses_drawdown_and_return():
    equity_curve = pd.Series([100.0, 130.0, 104.0, 150.0])

    result = metrics.calculate_calmar_ratio(equity_curve, periods_per_year=252)

    assert math.isfinite(result)


def test_trade_win_loss_statistics_use_numeric_pnl_values():
    trades = [10.0, -4.0, 6.0, -2.0]

    assert math.isclose(metrics.calculate_win_rate(trades), 0.5, rel_tol=1e-9)
    assert math.isclose(metrics.calculate_avg_win(trades), 8.0, rel_tol=1e-9)
    assert math.isclose(metrics.calculate_avg_loss(trades), -3.0, rel_tol=1e-9)
    assert math.isclose(metrics.calculate_profit_factor(trades), 16.0 / 6.0, rel_tol=1e-9)
    assert math.isclose(metrics.calculate_expectancy(trades), 2.5, rel_tol=1e-9)
    assert math.isclose(metrics.calculate_payoff_ratio(trades), 8.0 / 3.0, rel_tol=1e-9)


def test_recovery_factor_uses_total_return_and_drawdown():
    equity_curve = pd.Series([100.0, 120.0, 90.0, 130.0])

    result = metrics.calculate_recovery_factor(equity_curve, max_drawdown=0.25)

    assert math.isfinite(result)
    assert result > 0


def test_empty_trade_statistics_match_current_safe_defaults():
    trades = []

    assert metrics.calculate_win_rate(trades) == 0
    assert math.isinf(metrics.calculate_profit_factor(trades))
    assert metrics.calculate_expectancy(trades) == 0
    assert metrics.calculate_avg_win(trades) == 0
    assert metrics.calculate_avg_loss(trades) == 0
    assert math.isinf(metrics.calculate_payoff_ratio(trades))
