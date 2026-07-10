from datetime import datetime

from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection


def test_open_position_with_default_sizing_succeeds_with_typical_risk_config():
    """Regression test: the default risk-based sizing formula produced a
    notional ~2x capital for typical risk_pct/stop_loss_pct ratios, so
    open_position() with quantity=None silently failed (returned False, no
    error, no exception) for any caller relying on the default sizing."""
    engine = BacktestEngineV2(initial_capital=10000.0)

    opened = engine.open_position("BTC/USDT", OrderDirection.LONG, price=100.0, timestamp=datetime.now())

    assert opened is True
    assert len(engine.positions) == 1


def test_default_sizing_never_exceeds_available_capital():
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.001, slippage=0.0005)

    quantity = engine._calculate_position_size(capital=10000.0, price=100.0)

    cost = 100.0 * (1 + engine.slippage) * quantity * (1 + engine.commission)
    assert cost <= 10000.0


def test_default_sizing_still_respects_risk_amount_when_it_fits_in_capital():
    """When risk_pct/stop_loss_pct implies a notional well within capital,
    the risk-based sizing should still be used (not always clamped down)."""
    engine = BacktestEngineV2(initial_capital=10000.0)

    quantity = engine._calculate_position_size(capital=10000.0, price=100.0, risk_pct=0.01, stop_loss_pct=0.05)

    # risk_amount=100, stop_distance=5 -> quantity=20, notional=2000, well under capital
    assert quantity == 20.0


def test_closing_a_profitable_long_increases_capital():
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    ts = datetime(2026, 1, 1)
    engine.open_position("BTC/USDT", OrderDirection.LONG, price=100.0, timestamp=ts, quantity=10.0)

    trade = engine.close_position(engine.positions[0], price=110.0, timestamp=ts)

    assert trade.net_pnl > 0
    # compare the full round-trip against the ORIGINAL starting capital, not
    # the intermediate post-open value - capital is always tied up while a
    # position is open, so it naturally rises somewhat at close regardless
    # of profit or loss; only initial_capital is the correct baseline.
    assert engine.capital > engine.initial_capital


def test_closing_a_profitable_short_increases_capital():
    """Regression test: close_position() used to credit raw sale proceeds
    back to capital regardless of direction, so a SHORT that fell in price
    (a real profit, correctly reflected in Trade.net_pnl) still registered
    as a capital LOSS, because the pre-fix formula never accounted for the
    direction-based sign flip already applied to gross_pnl/net_pnl."""
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    ts = datetime(2026, 1, 1)
    engine.open_position("BTC/USDT", OrderDirection.SHORT, price=100.0, timestamp=ts, quantity=10.0)

    trade = engine.close_position(engine.positions[0], price=90.0, timestamp=ts)  # price fell -> short profits

    assert trade.net_pnl > 0
    assert engine.capital > engine.initial_capital  # capital must rise on a profitable short, not fall


def test_closing_a_losing_short_decreases_capital():
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    ts = datetime(2026, 1, 1)
    engine.open_position("BTC/USDT", OrderDirection.SHORT, price=100.0, timestamp=ts, quantity=10.0)

    trade = engine.close_position(engine.positions[0], price=110.0, timestamp=ts)  # price rose -> short loses

    assert trade.net_pnl < 0
    assert engine.capital < engine.initial_capital


def test_short_and_long_pnl_are_symmetric_for_the_same_price_move():
    """A LONG that gains $X when price rises by D, and a SHORT that gains
    $X when price falls by the same D, should produce the same net_pnl and
    the same capital increase - direction shouldn't change the magnitude,
    only which price move is profitable."""
    ts = datetime(2026, 1, 1)

    long_engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    long_engine.open_position("BTC/USDT", OrderDirection.LONG, price=100.0, timestamp=ts, quantity=10.0)
    long_trade = long_engine.close_position(long_engine.positions[0], price=110.0, timestamp=ts)

    short_engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    short_engine.open_position("BTC/USDT", OrderDirection.SHORT, price=110.0, timestamp=ts, quantity=10.0)
    short_trade = short_engine.close_position(short_engine.positions[0], price=100.0, timestamp=ts)

    assert long_trade.net_pnl == short_trade.net_pnl
    assert long_engine.capital == short_engine.capital


def test_update_equity_marks_an_unrealized_short_profit_correctly():
    """Regression test: update_equity() used to add raw current_price*quantity
    to positions_value regardless of direction, ignoring the direction-aware
    pos.pnl it had just computed one line earlier. For a SHORT, that meant
    an unrealized PROFIT (price falling) shrank reported equity instead of
    growing it - the mark-to-market equity curve (what Sharpe/drawdown are
    computed from) was wrong for every open short, not just at close."""
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    ts = datetime(2026, 1, 1)
    engine.open_position("BTC/USDT", OrderDirection.SHORT, price=100.0, timestamp=ts, quantity=10.0)

    total_value = engine.update_equity({"BTC/USDT": 90.0})  # price fell -> unrealized short profit

    assert total_value > engine.initial_capital


def test_update_equity_marks_an_unrealized_short_loss_correctly():
    engine = BacktestEngineV2(initial_capital=10000.0, commission=0.0, slippage=0.0)
    ts = datetime(2026, 1, 1)
    engine.open_position("BTC/USDT", OrderDirection.SHORT, price=100.0, timestamp=ts, quantity=10.0)

    total_value = engine.update_equity({"BTC/USDT": 110.0})  # price rose -> unrealized short loss

    assert total_value < engine.initial_capital
