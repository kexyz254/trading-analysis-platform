from trading_assistant.execution.quality import compute_slippage_pct, order_size_vs_volume_pct


class TestComputeSlippagePct:
    def test_buy_worse_fill_is_positive_slippage(self):
        # bought at 101 when the decision was made at 100 - paid more than intended
        assert compute_slippage_pct(intended_price=100.0, fill_price=101.0, side="buy") == 0.01

    def test_buy_better_fill_is_negative_slippage(self):
        assert compute_slippage_pct(intended_price=100.0, fill_price=99.0, side="buy") == -0.01

    def test_sell_worse_fill_is_positive_slippage(self):
        # sold at 99 when the decision was made at 100 - received less than intended
        assert compute_slippage_pct(intended_price=100.0, fill_price=99.0, side="sell") == 0.01

    def test_sell_better_fill_is_negative_slippage(self):
        assert compute_slippage_pct(intended_price=100.0, fill_price=101.0, side="sell") == -0.01

    def test_exact_fill_is_zero_slippage(self):
        assert compute_slippage_pct(intended_price=100.0, fill_price=100.0, side="buy") == 0.0

    def test_non_positive_intended_price_returns_none(self):
        assert compute_slippage_pct(intended_price=0.0, fill_price=100.0, side="buy") is None
        assert compute_slippage_pct(intended_price=-5.0, fill_price=100.0, side="buy") is None


class TestOrderSizeVsVolumePct:
    def test_computes_fraction_of_recent_volume(self):
        assert order_size_vs_volume_pct(order_notional=1000.0, recent_avg_volume_notional=10000.0) == 0.1

    def test_none_when_volume_unavailable(self):
        assert order_size_vs_volume_pct(order_notional=1000.0, recent_avg_volume_notional=0.0) is None
        assert order_size_vs_volume_pct(order_notional=1000.0, recent_avg_volume_notional=None) is None

    def test_none_for_negative_order_notional(self):
        assert order_size_vs_volume_pct(order_notional=-100.0, recent_avg_volume_notional=1000.0) is None
