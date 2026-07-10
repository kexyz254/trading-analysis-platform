from trading_assistant.risk.trailing_stop import (
    compute_trailing_stop_price,
    should_raise_stop,
    update_high_water_mark,
)


class TestUpdateHighWaterMark:
    def test_none_starts_tracking_from_current_price(self):
        assert update_high_water_mark(None, 100.0) == 100.0

    def test_advances_on_a_new_high(self):
        assert update_high_water_mark(100.0, 110.0) == 110.0

    def test_does_not_retreat_on_a_pullback(self):
        assert update_high_water_mark(110.0, 105.0) == 110.0

    def test_unchanged_on_an_exact_repeat(self):
        assert update_high_water_mark(100.0, 100.0) == 100.0


class TestComputeTrailingStopPrice:
    def test_computes_pct_below_high_water_mark(self):
        assert compute_trailing_stop_price(high_water_mark=100.0, trailing_pct=0.05) == 95.0

    def test_zero_trailing_pct_equals_high_water_mark(self):
        assert compute_trailing_stop_price(high_water_mark=100.0, trailing_pct=0.0) == 100.0


class TestShouldRaiseStop:
    def test_no_current_stop_always_raises(self):
        assert should_raise_stop(None, candidate_stop_price=95.0) is True

    def test_non_positive_current_stop_always_raises(self):
        assert should_raise_stop(0.0, candidate_stop_price=95.0) is True

    def test_meaningful_improvement_raises(self):
        assert should_raise_stop(90.0, candidate_stop_price=95.0) is True

    def test_lower_candidate_never_raises(self):
        assert should_raise_stop(95.0, candidate_stop_price=90.0) is False

    def test_equal_candidate_does_not_raise(self):
        assert should_raise_stop(95.0, candidate_stop_price=95.0) is False

    def test_tiny_improvement_below_threshold_does_not_raise(self):
        current = 100.0
        candidate = 100.05  # 0.05% improvement, below the 0.1% default threshold
        assert should_raise_stop(current, candidate) is False

    def test_custom_min_improvement_threshold_respected(self):
        current = 100.0
        candidate = 100.05
        assert should_raise_stop(current, candidate, min_improvement_pct=0.0001) is True
