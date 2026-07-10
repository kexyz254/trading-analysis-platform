from trading_assistant.risk.backtest_sizing import risk_based_quantity


def test_risk_based_quantity_matches_risk_manager_formula():
    # risk_amount = 10000 * 0.02 = 200; notional = 200 / 0.01 = 20000,
    # capped at capital * max_position_size = 10000 * 0.1 = 1000.
    quantity = risk_based_quantity(
        capital=10000.0, price=100.0, stop_loss_pct=0.01, risk_per_trade=0.02, max_position_size=0.1
    )

    assert quantity == 10.0  # 1000 notional / 100 price


def test_risk_based_quantity_uncapped_case():
    # risk_amount = 10000 * 0.01 = 100; notional = 100 / 0.05 = 2000, well
    # under the max_position_size cap of 5000 - risk_per_trade/stop_pct binds.
    quantity = risk_based_quantity(
        capital=10000.0, price=50.0, stop_loss_pct=0.05, risk_per_trade=0.01, max_position_size=0.5
    )

    assert quantity == 40.0  # 2000 / 50


def test_risk_based_quantity_is_zero_for_invalid_inputs():
    assert risk_based_quantity(capital=0.0, price=100.0, stop_loss_pct=0.01) == 0.0
    assert risk_based_quantity(capital=10000.0, price=0.0, stop_loss_pct=0.01) == 0.0
    assert risk_based_quantity(capital=10000.0, price=100.0, stop_loss_pct=0.0) == 0.0


def test_risk_based_quantity_is_much_smaller_than_the_old_flat_95pct_sizing():
    """The core parity gap this closes: the old backtest sizing deployed
    ~95% of capital every trade regardless of risk; risk-based sizing at
    realistic defaults is dramatically smaller, so a strategy's walk-forward
    numbers reflect the risk level it will actually be traded at live."""
    old_flat_quantity = (10000.0 * 0.95) / 100.0

    quantity = risk_based_quantity(capital=10000.0, price=100.0, stop_loss_pct=0.01)

    assert quantity < old_flat_quantity / 5
