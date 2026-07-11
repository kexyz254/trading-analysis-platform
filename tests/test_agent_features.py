import pandas as pd

from trading_assistant.agents.features import (
    FEATURE_COLUMNS,
    build_features,
    classify_market_condition,
    classify_regime,
    label_next_bar_direction,
)


def _ohlcv(closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


def test_build_features_adds_all_expected_columns():
    frame = _ohlcv([float(i) for i in range(1, 41)])

    featured = build_features(frame)

    for column in FEATURE_COLUMNS:
        assert column in featured.columns
    # original OHLCV columns must survive so the frame stays usable downstream
    assert "close" in featured.columns


def test_build_features_last_row_is_populated_with_enough_history():
    frame = _ohlcv([float(i) for i in range(1, 41)])

    featured = build_features(frame)

    last_row = featured.iloc[-1]
    assert last_row[FEATURE_COLUMNS].notna().all()


def test_label_next_bar_direction_matches_future_close():
    frame = _ohlcv([100.0, 101.0, 99.0, 99.0])

    labels = label_next_bar_direction(frame)

    assert list(labels) == [1, 0, 0, 0]  # last row's "next" is NaN -> False -> 0


def test_bounded_oscillators_stay_within_their_mathematical_range():
    """Stochastic %K/%D and RSI are mathematically bounded to [0, 100] before
    normalization - unlike most features here, this is a hard guarantee
    worth directly checking, not just a design preference."""
    frame = _ohlcv([50.0 + 10.0 * ((i % 7) - 3) for i in range(60)])  # oscillating, not monotonic

    featured = build_features(frame).dropna(subset=["stochastic_k", "stochastic_d", "rsi_normalized"])

    assert (featured["stochastic_k"].between(0, 1)).all()
    assert (featured["stochastic_d"].between(0, 1)).all()
    assert (featured["rsi_normalized"].between(0, 1)).all()


def test_new_indicator_features_are_finite_on_a_realistic_series():
    """Regression coverage for the expanded feature set (Stochastic, CCI,
    ADX, Vortex, Donchian, Keltner, OBV, VWAP, candle shape, session) -
    every one must be a finite number by the time enough history has
    accumulated, not NaN/inf leaking through from a division-by-zero edge
    case in an under-tested indicator."""
    closes = [100.0 + i * 0.3 + 5.0 * ((i % 5) - 2) for i in range(80)]
    frame = _ohlcv(closes)

    featured = build_features(frame)
    last_row = featured.iloc[-1]

    new_columns = [
        "stochastic_k", "stochastic_d", "cci_normalized", "roc_pct",
        "adx_strength", "adx_directional", "vortex_diff",
        "donchian_position", "keltner_position",
        "relative_volume", "obv_zscore",
        "return_10", "return_20", "volatility_10",
        "candle_body_ratio", "upper_wick_ratio", "lower_wick_ratio",
        "price_vs_vwap", "hour_sin", "hour_cos",
    ]
    for column in new_columns:
        value = last_row[column]
        assert pd.notna(value), f"{column} is NaN"
        assert abs(value) != float("inf"), f"{column} is inf"


def test_smc_features_are_bounded_and_not_trivially_constant():
    """Regression coverage for the Smart Money Concepts feature block
    (smc_structure_state, smc_bos, smc_mss, smc_in_bullish_ob_zone,
    smc_in_bearish_ob_zone) - a genuinely oscillating series (not the
    monotonic fixture other tests use, which would never actually form
    a swing point) must produce values within each feature's documented
    range, and the signed/directional ones must not be silently stuck at
    zero throughout - that would mean the whole block quietly never
    fires on real data despite computing without error."""
    closes = [100.0 + 20.0 * ((i % 10) - 5) + i * 0.1 for i in range(150)]
    frame = _ohlcv(closes)

    featured = build_features(frame).dropna(subset=FEATURE_COLUMNS)

    assert featured["smc_structure_state"].isin([-1.0, 0.0, 1.0]).all()
    assert featured["smc_bos"].isin([-1.0, 0.0, 1.0]).all()
    assert featured["smc_mss"].isin([-1.0, 0.0, 1.0]).all()
    assert featured["smc_in_bullish_ob_zone"].isin([0.0, 1.0]).all()
    assert featured["smc_in_bearish_ob_zone"].isin([0.0, 1.0]).all()
    # An oscillating 150-bar series must actually reach a non-ranging
    # structure state at some point - otherwise swing detection itself
    # is silently broken on real data.
    assert set(featured["smc_structure_state"].unique()) != {0.0}


def test_price_vs_session_vwap_is_not_trivially_constant():
    """Regression coverage for the session-anchored VWAP feature (see
    trading_assistant.analysis.vwap) - an oscillating series must
    produce a genuinely varying deviation from session VWAP, not a
    constant (which would mean the session-reset grouping silently
    collapsed to a single group, or vwap computation is broken)."""
    closes = [100.0 + 20.0 * ((i % 10) - 5) + i * 0.1 for i in range(150)]
    frame = _ohlcv(closes)

    featured = build_features(frame).dropna(subset=FEATURE_COLUMNS)

    assert featured["price_vs_session_vwap"].nunique() > 1


def test_classify_regime_requires_strong_adx_to_call_a_trend():
    # strong ADX (trending market) - direction sign decides up vs down
    assert classify_regime(trend_strength=0.05, adx_strength=0.40) == "trending_up"
    assert classify_regime(trend_strength=-0.05, adx_strength=0.40) == "trending_down"
    # weak ADX (choppy/ranging market) - direction is noise, always ranging
    assert classify_regime(trend_strength=0.05, adx_strength=0.10) == "ranging"
    assert classify_regime(trend_strength=-0.05, adx_strength=0.10) == "ranging"


def test_classify_regime_treats_nan_as_ranging():
    assert classify_regime(trend_strength=float("nan"), adx_strength=0.40) == "ranging"
    assert classify_regime(trend_strength=0.05, adx_strength=float("nan")) == "ranging"


class TestClassifyMarketCondition:
    def test_normal_conditions_return_none(self):
        assert classify_market_condition(atr_pct=0.02, relative_volume=1.0) is None

    def test_extreme_atr_is_panic(self):
        assert classify_market_condition(atr_pct=0.10, relative_volume=1.0) == "panic"

    def test_thin_volume_is_illiquid(self):
        assert classify_market_condition(atr_pct=0.02, relative_volume=0.1) == "illiquid"

    def test_panic_takes_priority_over_illiquid_when_both_apply(self):
        assert classify_market_condition(atr_pct=0.10, relative_volume=0.1) == "panic"

    def test_boundary_values(self):
        assert classify_market_condition(atr_pct=0.08, relative_volume=1.0) == "panic"
        assert classify_market_condition(atr_pct=0.02, relative_volume=0.3) == "illiquid"

    def test_nan_inputs_return_none(self):
        assert classify_market_condition(atr_pct=float("nan"), relative_volume=1.0) is None
        assert classify_market_condition(atr_pct=0.02, relative_volume=float("nan")) is None

    def test_custom_thresholds_respected(self):
        assert classify_market_condition(atr_pct=0.05, relative_volume=1.0, panic_atr_threshold=0.04) == "panic"
        assert classify_market_condition(atr_pct=0.05, relative_volume=1.0, panic_atr_threshold=0.20) is None
