"""Tests for portfolio management."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.portfolio.manager import (
    PortfolioManager,
    RiskManager,
    performance_summary_from_trade_pnls,
    wilson_lower_bound,
)

def test_portfolio_manager_open_position():
    pm = PortfolioManager(10000)
    result = pm.open_position('AAPL', 100, 10)
    assert result is True
    assert pm.capital == 9000

def test_portfolio_manager_close_position():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pnl = pm.close_position('AAPL', 110)
    assert pnl == 100
    assert pm.capital == 10100

def test_portfolio_manager_stop_loss():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10, stop_loss=95)
    triggered = pm.check_stop_loss_take_profit('AAPL', 94)
    assert triggered is True
    assert 'AAPL' not in pm.positions

def test_close_position_with_no_quantity_closes_everything():
    """Unchanged behavior for every existing caller: quantity=None closes
    the whole position, exactly as before partial closes existed."""
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pnl = pm.close_position('AAPL', 110)
    assert pnl == 100
    assert 'AAPL' not in pm.positions


def test_partial_close_reduces_quantity_and_leaves_position_open():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)  # cost = 1000

    pnl = pm.close_position('AAPL', 110, quantity=4)

    assert pnl == 40  # 4 * (110 - 100)
    assert 'AAPL' in pm.positions
    assert pm.positions['AAPL']['quantity'] == 6
    assert pm.positions['AAPL']['cost'] == 600  # proportional cost basis remaining


def test_partial_close_capital_reflects_only_the_sold_portion():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)  # capital -> 9000

    pm.close_position('AAPL', 110, quantity=4)

    assert pm.capital == 9000 + 110 * 4  # 9440


def test_partial_close_quantity_larger_than_position_closes_everything():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)

    pnl = pm.close_position('AAPL', 110, quantity=999)

    assert pnl == 100
    assert 'AAPL' not in pm.positions


def test_sequential_partial_closes_eventually_close_the_full_position():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)

    first_pnl = pm.close_position('AAPL', 110, quantity=6)
    second_pnl = pm.close_position('AAPL', 120, quantity=4)

    assert first_pnl == 60  # 6 * 10
    assert second_pnl == 80  # 4 * 20
    assert 'AAPL' not in pm.positions
    assert pm.capital == 10000 - 1000 + 110 * 6 + 120 * 4


def test_zero_or_negative_partial_quantity_is_a_no_op():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)

    assert pm.close_position('AAPL', 110, quantity=0) is None
    assert pm.close_position('AAPL', 110, quantity=-1) is None
    assert pm.positions['AAPL']['quantity'] == 10  # unchanged


def test_partial_close_with_friction_charges_commission_only_on_sold_portion():
    pm = PortfolioManager(10000, commission_rate=0.01)
    pm.open_position('AAPL', 100, 10)  # cost = 1000 + 10 commission = 1010

    pnl = pm.close_position('AAPL', 110, quantity=5)  # proceeds 550, commission 5.5 -> net 544.5

    assert pnl == pytest.approx(544.5 - 505)  # net proceeds - proportional cost basis (1010/2)


def test_portfolio_manager_zero_friction_default_matches_original_behavior():
    """Regression: commission_rate/slippage_rate must default to 0.0 so every
    existing caller (real execution included) that never asked for friction
    keeps behaving exactly as before this was added."""
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    assert pm.capital == 9000  # unchanged from test_portfolio_manager_open_position
    pnl = pm.close_position('AAPL', 110)
    assert pnl == 100  # unchanged from test_portfolio_manager_close_position


def test_commission_reduces_realized_pnl():
    pm = PortfolioManager(10000, commission_rate=0.001)
    pm.open_position('AAPL', 100, 10)  # cost = 1000, commission = 1.0 -> capital = 8999
    assert pm.capital == pytest.approx(8999.0)
    pnl = pm.close_position('AAPL', 110)  # proceeds = 1100, commission = 1.1 -> net 1098.9
    # pnl = net_proceeds - cost = 1098.9 - 1001.0
    assert pnl == pytest.approx(97.9)


def test_slippage_moves_fill_price_against_the_trader():
    pm = PortfolioManager(10000, slippage_rate=0.01)  # 1% slippage, exaggerated for a clear test
    pm.open_position('AAPL', 100, 10)  # buy fills at 100 * 1.01 = 101, cost = 1010
    assert pm.capital == pytest.approx(8990.0)
    pnl = pm.close_position('AAPL', 100)  # sell fills at 100 * 0.99 = 99, proceeds = 990
    assert pnl == pytest.approx(990.0 - 1010.0)  # -20, a round trip at an UNCHANGED quoted price still loses money


def test_round_trip_at_break_even_quoted_price_is_a_real_loss_with_friction():
    """The clearest possible proof friction is real: buying and selling at
    the EXACT SAME quoted price must be a net loss once commission and
    slippage are both switched on - with zero friction this would be
    exactly $0 P&L, which is precisely the gap this whole audit is about."""
    pm = PortfolioManager(10000, commission_rate=0.001, slippage_rate=0.0005)
    pm.open_position('BTC/USDT', 60000, 0.1)
    pnl = pm.close_position('BTC/USDT', 60000)  # identical quoted entry/exit price
    assert pnl < 0  # a real loss purely from round-trip friction, not a market move


def test_risk_manager_position_size():
    rm = RiskManager(max_position_size=0.1)
    size = rm.calculate_position_size(10000, 0.02, 0.01)
    assert size > 0
    assert size <= 1000

def test_risk_manager_kelly():
    rm = RiskManager()
    kelly = rm.calculate_kelly_fraction(0.6, 2, 1)
    assert 0.2 < kelly < 0.3


def test_wilson_lower_bound_is_below_the_point_estimate_for_a_small_sample():
    """The core shrinkage property: a small sample's lower bound must sit
    meaningfully below its raw point estimate - e.g. 6/10 wins (60%) is
    genuinely uncertain, the lower bound should reflect that."""
    point_estimate = 6 / 10
    lower_bound = wilson_lower_bound(successes=6, n=10)
    assert lower_bound < point_estimate
    assert lower_bound > 0.0  # not absurdly conservative either


def test_wilson_lower_bound_converges_toward_the_point_estimate_as_n_grows():
    """Same 60% win rate, far larger sample - the interval narrows, so the
    lower bound should sit much closer to the raw point estimate than the
    small-sample case above."""
    small_sample_bound = wilson_lower_bound(successes=6, n=10)
    large_sample_bound = wilson_lower_bound(successes=600, n=1000)

    assert large_sample_bound > small_sample_bound
    assert large_sample_bound == pytest.approx(0.6, abs=0.03)


def test_wilson_lower_bound_handles_zero_and_perfect_samples():
    assert wilson_lower_bound(successes=0, n=0) == 0.0
    assert wilson_lower_bound(successes=0, n=10) == 0.0
    assert 0.0 <= wilson_lower_bound(successes=10, n=10) <= 1.0  # never exceeds [0, 1]

def test_risk_manager_daily_loss_resets_on_new_day():
    rm = RiskManager(max_daily_loss=0.02)
    from datetime import date, timedelta

    day_one = date(2026, 1, 1)
    rm.record_trade_pnl(-0.015, today=day_one)
    assert rm.check_daily_loss_limit() is True
    rm.record_trade_pnl(-0.01, today=day_one)
    assert rm.check_daily_loss_limit() is False

    day_two = day_one + timedelta(days=1)
    rm.reset_if_new_day(day_two)
    assert rm.daily_pnl == 0.0
    assert rm.check_daily_loss_limit() is True

def test_risk_manager_record_trade_pnl_accumulates_same_day():
    rm = RiskManager(max_daily_loss=0.05)
    from datetime import date

    today = date(2026, 1, 1)
    rm.record_trade_pnl(-0.01, today=today)
    rm.record_trade_pnl(-0.02, today=today)
    assert rm.daily_pnl == pytest.approx(-0.03)

def test_portfolio_manager_get_total_value():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    current_prices = {'AAPL': 105}
    total_value = pm.get_total_value(current_prices)
    assert abs(total_value - 10050) < 1

def test_portfolio_manager_get_unrealized_pnl():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pnl = pm.get_position_pnl('AAPL', 105)
    assert pnl == 50


def test_get_total_value_excludes_unexplained_quantity():
    """Accounting regression: a position reconciled from a real exchange
    balance the journal couldn't fully explain (e.g. testnet faucet money)
    must not have its current market value counted as strategy equity -
    see PortfolioManager._tracked_quantity / PortfolioAgent.reconcile_with_exchange."""
    pm = PortfolioManager(10000.0)
    pm.positions['WBTC/USDT'] = {
        'price': 60000.0, 'quantity': 1.0, 'cost': 60000.0, 'unexplained_quantity': 1.0,
    }

    total_value = pm.get_total_value({'WBTC/USDT': 65000.0})

    assert total_value == pytest.approx(10000.0)  # the full $65k is excluded, none of it is tracked capital


def test_get_total_value_counts_only_the_explained_portion_of_a_mixed_position():
    pm = PortfolioManager(10000.0)
    pm.positions['XRP/USDT'] = {
        'price': 1.4, 'quantity': 1000.0, 'cost': 1400.0, 'unexplained_quantity': 400.0,
    }

    total_value = pm.get_total_value({'XRP/USDT': 2.0})

    assert total_value == pytest.approx(10000.0 + 600.0 * 2.0)  # only the explained 600 XRP counts


def test_get_total_value_unaffected_when_unexplained_quantity_absent():
    """Backward compatible: every position opened through the normal
    open_position path has no unexplained_quantity key at all - must
    behave exactly as before this field existed."""
    pm = PortfolioManager(10000.0)
    pm.open_position('AAPL', 100, 10)

    assert pm.get_total_value({'AAPL': 105.0}) == pytest.approx(9000.0 + 1050.0)


def test_get_position_pnl_excludes_unexplained_quantity():
    pm = PortfolioManager(10000.0)
    pm.positions['WBTC/USDT'] = {
        'price': 60000.0, 'quantity': 1.0, 'cost': 60000.0, 'unexplained_quantity': 1.0,
    }

    assert pm.get_position_pnl('WBTC/USDT', 65000.0) == pytest.approx(0.0)

def test_portfolio_manager_performance_summary():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pm.close_position('AAPL', 110)
    summary = pm.get_performance_summary()
    assert summary['total_trades'] == 1
    assert summary['total_pnl'] == 100

def test_performance_summary_profit_factor_is_json_safe_with_no_losing_trades():
    # Real incident: every completed real trade netted exactly $0.00 PnL (a
    # batch of liquidated faucet-seeded testnet positions), so there were no
    # losing trades - profit_factor used to be float("inf") here, which
    # Starlette's JSONResponse (allow_nan=False) refuses to serialize and
    # 500s the whole /api/real-performance endpoint. None is JSON-safe and
    # also the more honest value for an undefined ratio.
    summary = performance_summary_from_trade_pnls([0.0, 0.0, 5.0])
    assert summary['profit_factor'] is None
    import json
    json.dumps(summary, allow_nan=False)  # must not raise


def test_portfolio_manager_max_drawdown():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 11000, 10500, 12000, 11500]
    max_dd = pm.get_max_drawdown()
    assert isinstance(max_dd, float)

def test_portfolio_manager_sharpe_ratio():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 10100, 10200, 10150, 10300]
    sharpe = pm.get_sharpe_ratio()
    assert isinstance(sharpe, float)


def test_sharpe_ratio_is_zero_not_absurd_for_a_perfectly_flat_equity_curve():
    """Regression: a genuinely flat curve (e.g. a freshly-started paper-
    trading candidate with zero trades) used to produce a Sharpe in the
    tens of quadrillions - np.std on a set of identical floats can leave a
    ~1e-20 non-zero residual from floating-point summation order, not real
    volatility, and dividing by it blew up. Must read as "no signal yet",
    not an extreme value."""
    pm = PortfolioManager(10000.0)
    pm.equity_curve = [1000.0] * 20

    sharpe = pm.get_sharpe_ratio()

    assert sharpe == 0.0

def test_portfolio_manager_calmar_ratio():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 11000, 10500, 12000, 11500]
    calmar = pm.get_calmar_ratio()
    assert isinstance(calmar, float)


def test_close_position_does_not_append_a_cash_only_equity_point():
    """Regression: close_position used to append self.capital (cash ONLY)
    to equity_curve via _update_equity_curve, while every real caller
    (PortfolioAgent.mark_to_market, ChampionChallengerAgent) appends
    get_total_value (cash + all open positions). With two+ symbols open,
    closing one spliced a fabricated low point into the curve - a real
    drawdown-from-bookkeeping artifact, not a real loss. close_position
    must leave equity_curve untouched; only the caller's own
    append_equity_point(get_total_value(...)) call should update it."""
    pm = PortfolioManager(10000.0)
    pm.open_position('BTC/USDT', 100.0, 5.0)   # cost 500
    pm.open_position('ETH/USDT', 50.0, 4.0)    # cost 200
    starting_length = len(pm.equity_curve)

    pm.close_position('BTC/USDT', 100.0)  # flat PnL close

    assert len(pm.equity_curve) == starting_length  # no cash-only point appended
    # BTC round-tripped at flat PnL (capital back to 9800) and ETH (200) was
    # never touched - total value is exactly the original 10000, unchanged.
    assert pm.get_total_value({'ETH/USDT': 50.0}) == pytest.approx(10000.0)


def test_close_position_on_unexplained_balance_does_not_inflate_capital():
    """Regression: a position reconciled from a real exchange balance the
    journal couldn't explain (e.g. testnet faucet money) was never counted
    as capital while held (unexplained_quantity excludes it from
    get_total_value) - but selling it used to credit the FULL sale
    proceeds to capital, injecting a one-time phantom capital jump for
    money the strategy never actually had."""
    pm = PortfolioManager(10000.0)
    pm.positions['WBTC/USDT'] = {
        'price': 60000.0, 'quantity': 1.0, 'cost': 60000.0, 'unexplained_quantity': 1.0,
    }

    pnl = pm.close_position('WBTC/USDT', 65000.0)

    assert pm.capital == pytest.approx(10000.0)  # fully unexplained - none of the sale proceeds are real capital
    assert pnl == pytest.approx(0.0)
    assert 'WBTC/USDT' not in pm.positions


def test_close_position_on_mixed_position_credits_only_the_explained_share():
    """The explained (600 @ $1.0 = $600) and unexplained (400 @ $2.0
    reconciliation price = $800) sub-portions are priced DIFFERENTLY -
    close_position must isolate the explained sub-portion's own PnL, not
    take a flat proportional slice of the blended $1400 total (which would
    misattribute PnL between the two differently-priced sub-portions)."""
    pm = PortfolioManager(10000.0)
    pm.positions['XRP/USDT'] = {
        'price': 1.4, 'quantity': 1000.0, 'cost': 1400.0, 'unexplained_quantity': 400.0,
        'explained_cost': 600.0,  # 600 units bought @ $1.0
    }

    pnl = pm.close_position('XRP/USDT', 2.0)

    explained_proceeds = 600.0 * 2.0
    explained_cost = 600.0
    assert pm.capital == pytest.approx(10000.0 + explained_proceeds)
    assert pnl == pytest.approx(explained_proceeds - explained_cost)


def test_partial_close_on_mixed_position_shrinks_unexplained_quantity_proportionally():
    """Regression: a partial close used to leave unexplained_quantity
    untouched while quantity shrank, eventually driving _tracked_quantity
    (quantity - unexplained_quantity) NEGATIVE - inverting the sign of
    live equity/PnL for the remainder of the position."""
    pm = PortfolioManager(10000.0)
    pm.positions['XRP/USDT'] = {
        'price': 1.09, 'quantity': 1000.0, 'cost': 1090.0, 'unexplained_quantity': 900.0,
        'explained_cost': 100.0,  # 100 units bought @ $1.0 (10% explained)
    }

    pm.close_position('XRP/USDT', 2.0, quantity=500.0)  # sell half

    remaining = pm.positions['XRP/USDT']
    assert remaining['quantity'] == 500.0
    assert remaining['unexplained_quantity'] == pytest.approx(450.0)  # shrank proportionally, not left at 900
    assert pm._tracked_quantity('XRP/USDT') == pytest.approx(50.0)  # stayed positive and proportionate


def test_equity_curve_is_bounded_over_a_long_run():
    """24/7 memory-leak regression guard: equity_curve must never grow
    without bound - the journal already durably persists the full history,
    every live Sharpe/drawdown/readiness calculation already reads from
    THERE, not this in-memory list, so trimming it costs nothing real."""
    pm = PortfolioManager(10000.0)

    for i in range(PortfolioManager.MAX_HISTORY_LENGTH + 500):
        pm.append_equity_point(10000.0 + i)

    assert len(pm.equity_curve) == PortfolioManager.MAX_HISTORY_LENGTH
    assert pm.equity_curve[-1] == 10000.0 + PortfolioManager.MAX_HISTORY_LENGTH + 499  # most recent kept
    assert pm.equity_curve[0] == 10000.0 + 500  # oldest 500 trimmed from the front


def test_append_equity_point_below_cap_keeps_everything():
    pm = PortfolioManager(10000.0)

    for i in range(10):
        pm.append_equity_point(10000.0 + i)

    assert len(pm.equity_curve) == 11  # initial seed value + 10 appended


def test_trade_history_is_bounded_over_a_long_run():
    pm = PortfolioManager(10000.0)

    for i in range(PortfolioManager.MAX_HISTORY_LENGTH + 500):
        pm._record_trade("buy", "BTC/USDT", 100.0 + i, 1.0)

    assert len(pm.trade_history) == PortfolioManager.MAX_HISTORY_LENGTH
    assert pm.trade_history[-1]["price"] == 100.0 + PortfolioManager.MAX_HISTORY_LENGTH + 499
    assert pm.trade_history[0]["price"] == 100.0 + 500


def test_update_equity_curve_uses_the_bounded_path():
    """Regression guard: _update_equity_curve (the internal mark-to-market
    path) must go through append_equity_point, not append to the raw list
    directly, or it would silently bypass the cap."""
    pm = PortfolioManager(10000.0)
    pm.capital = 12000.0

    for _ in range(PortfolioManager.MAX_HISTORY_LENGTH + 10):
        pm._update_equity_curve()

    assert len(pm.equity_curve) == PortfolioManager.MAX_HISTORY_LENGTH
