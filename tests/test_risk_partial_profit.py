from trading_assistant.risk.partial_profit import should_take_partial_profit, unrealized_pnl_pct


class TestUnrealizedPnlPct:
    def test_computes_pct_gain(self):
        assert unrealized_pnl_pct(entry_price=100.0, current_price=110.0) == 0.1

    def test_computes_pct_loss(self):
        assert unrealized_pnl_pct(entry_price=100.0, current_price=90.0) == -0.1

    def test_non_positive_entry_price_returns_zero(self):
        assert unrealized_pnl_pct(entry_price=0.0, current_price=110.0) == 0.0
        assert unrealized_pnl_pct(entry_price=-5.0, current_price=110.0) == 0.0


class TestShouldTakePartialProfit:
    def test_disabled_when_threshold_is_none(self):
        assert should_take_partial_profit(100.0, 200.0, partial_profit_pct=None, already_taken=False) is False

    def test_fires_once_profit_crosses_threshold(self):
        assert should_take_partial_profit(100.0, 105.0, partial_profit_pct=0.05, already_taken=False) is True

    def test_does_not_fire_below_threshold(self):
        assert should_take_partial_profit(100.0, 104.0, partial_profit_pct=0.05, already_taken=False) is False

    def test_does_not_fire_twice(self):
        assert should_take_partial_profit(100.0, 200.0, partial_profit_pct=0.05, already_taken=True) is False

    def test_exact_threshold_boundary_fires(self):
        assert should_take_partial_profit(100.0, 105.0, partial_profit_pct=0.05, already_taken=False) is True
