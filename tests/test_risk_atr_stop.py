from trading_assistant.risk.atr_stop import DEFAULT_MAX_STOP_PCT, DEFAULT_MIN_STOP_PCT, compute_atr_stop_pct


class TestComputeAtrStopPct:
    def test_scales_with_atr_and_multiplier(self):
        assert compute_atr_stop_pct(atr_pct=0.01, fallback_stop_pct=0.01, atr_multiplier=2.0) == 0.02

    def test_falls_back_when_atr_missing(self):
        assert compute_atr_stop_pct(None, fallback_stop_pct=0.015) == 0.015

    def test_falls_back_on_nan(self):
        assert compute_atr_stop_pct(float("nan"), fallback_stop_pct=0.015) == 0.015

    def test_falls_back_on_non_positive_atr(self):
        assert compute_atr_stop_pct(0.0, fallback_stop_pct=0.015) == 0.015
        assert compute_atr_stop_pct(-0.01, fallback_stop_pct=0.015) == 0.015

    def test_clamped_to_minimum_for_a_near_stablecoin_pair(self):
        # tiny ATR (e.g. a stablecoin pair) would otherwise produce an
        # effectively-zero stop distance
        stop = compute_atr_stop_pct(atr_pct=0.0001, fallback_stop_pct=0.01, min_stop_pct=0.005)
        assert stop == 0.005

    def test_clamped_to_maximum_for_a_wildly_volatile_pair(self):
        stop = compute_atr_stop_pct(atr_pct=0.5, fallback_stop_pct=0.01, max_stop_pct=0.05)
        assert stop == 0.05

    def test_default_clamp_bounds_are_exported_and_sane(self):
        assert DEFAULT_MIN_STOP_PCT < DEFAULT_MAX_STOP_PCT
