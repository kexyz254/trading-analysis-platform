from trading_assistant.portfolio.cash_flows import classify_balance_change


class TestClassifyBalanceChange:
    def test_a_withdrawal_with_no_trading_is_flagged_external(self):
        # The real incident this module exists for: a manual Binance
        # Predictions bet dropped free balance by 99.6% with zero trading
        # activity - this must be recognized as external, not a loss.
        result = classify_balance_change(
            previous_capital=2.91, new_capital=0.01, trading_cash_flow_since_last_sync=0.0
        )

        assert result.is_external_flow is True
        assert result.unexplained_delta < 0

    def test_a_deposit_with_no_trading_is_flagged_external(self):
        result = classify_balance_change(
            previous_capital=100.0, new_capital=500.0, trading_cash_flow_since_last_sync=0.0
        )

        assert result.is_external_flow is True
        assert result.unexplained_delta > 0

    def test_a_real_trading_loss_fully_explained_by_a_sell_is_not_external(self):
        result = classify_balance_change(
            previous_capital=100.0, new_capital=80.0, trading_cash_flow_since_last_sync=-20.0
        )

        assert result.is_external_flow is False
        assert result.unexplained_delta == 0.0

    def test_a_buy_moving_cash_into_a_position_is_not_external(self):
        # A buy is a real, large cash outflow but fully explained by trading -
        # it must never be misclassified as an external withdrawal.
        result = classify_balance_change(
            previous_capital=100.0, new_capital=40.0, trading_cash_flow_since_last_sync=-60.0
        )

        assert result.is_external_flow is False

    def test_small_fee_rounding_noise_does_not_count_as_external(self):
        result = classify_balance_change(
            previous_capital=100.0, new_capital=99.85, trading_cash_flow_since_last_sync=-0.10
        )

        assert result.is_external_flow is False

    def test_a_partial_mismatch_beyond_the_threshold_is_still_flagged(self):
        # Trading explains most of the change, but a meaningful gap remains -
        # e.g. a partial withdrawal alongside real trading in the same cycle.
        result = classify_balance_change(
            previous_capital=1000.0, new_capital=700.0, trading_cash_flow_since_last_sync=-50.0
        )

        assert result.is_external_flow is True
        assert result.unexplained_delta == -250.0

    def test_threshold_scales_with_capital_not_just_a_fixed_floor(self):
        # 5% of a large capital base is bigger than the $0.50 floor - a small
        # dollar mismatch that would flag at tiny capital must not falsely
        # flag at large capital.
        result = classify_balance_change(
            previous_capital=10_000.0, new_capital=10_000.0 - 100.0, trading_cash_flow_since_last_sync=-100.0 + 60.0
        )
        # unexplained_delta = -40, well under 5% of 10,000 (=500)
        assert result.is_external_flow is False
