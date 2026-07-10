from trading_assistant.execution.order_type import choose_order_type, marketable_limit_price


class TestChooseOrderType:
    def test_tight_spread_chooses_limit(self):
        assert choose_order_type(spread_pct=0.002, max_spread_pct=0.01, tight_spread_fraction=0.3) == "limit"

    def test_wide_but_acceptable_spread_chooses_market(self):
        assert choose_order_type(spread_pct=0.008, max_spread_pct=0.01, tight_spread_fraction=0.3) == "market"

    def test_exactly_at_the_boundary_chooses_limit(self):
        assert choose_order_type(spread_pct=0.003, max_spread_pct=0.01, tight_spread_fraction=0.3) == "limit"

    def test_no_max_spread_configured_falls_back_to_market(self):
        assert choose_order_type(spread_pct=0.001, max_spread_pct=None) == "market"

    def test_no_spread_data_falls_back_to_market(self):
        assert choose_order_type(spread_pct=None, max_spread_pct=0.01) == "market"


class TestMarketableLimitPrice:
    def test_buy_prices_slightly_above_current(self):
        price = marketable_limit_price(100.0, "buy", buffer_pct=0.001)
        assert price == 100.1

    def test_sell_prices_slightly_below_current(self):
        price = marketable_limit_price(100.0, "sell", buffer_pct=0.001)
        assert price == 99.9
