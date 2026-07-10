import pandas as pd
import pytest

from trading_assistant.risk.timeframe_confluence import (
    higher_timeframe_blocks_buy,
    higher_timeframe_regime,
    resample_ohlcv,
)


def _ohlcv(n, start=100.0, step=0.0):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100.0 + i for i in range(n)],
        }
    )


def test_resample_aggregates_ohlcv_correctly():
    frame = _ohlcv(8, start=100.0, step=1.0)  # closes: 100..107

    resampled = resample_ohlcv(frame, factor=4)

    assert len(resampled) == 2
    first_group = frame.iloc[0:4]
    assert resampled.iloc[0]["open"] == first_group.iloc[0]["open"]
    assert resampled.iloc[0]["close"] == first_group.iloc[-1]["close"]
    assert resampled.iloc[0]["high"] == first_group["high"].max()
    assert resampled.iloc[0]["low"] == first_group["low"].min()
    assert resampled.iloc[0]["volume"] == pytest.approx(first_group["volume"].sum())


def test_resample_trims_a_partial_leading_group_not_a_trailing_one():
    frame = _ohlcv(10, start=100.0, step=1.0)  # 10 bars, factor 4 -> 8 usable, 2 trimmed from the FRONT

    resampled = resample_ohlcv(frame, factor=4)

    assert len(resampled) == 2
    # the last resampled bar's close must be the very last original bar's close -
    # the most recent, most decision-relevant data must never be the part trimmed
    assert resampled.iloc[-1]["close"] == frame.iloc[-1]["close"]


def test_resample_returns_empty_when_fewer_bars_than_factor():
    frame = _ohlcv(3)
    assert resample_ohlcv(frame, factor=4).empty


def test_higher_timeframe_regime_none_with_insufficient_history():
    frame = _ohlcv(50, step=1.0)  # 50 1h bars -> only 12 resampled 4h bars, below MIN_RESAMPLED_BARS
    assert higher_timeframe_regime(frame, factor=4) is None


def test_higher_timeframe_regime_detects_a_clear_downtrend():
    # 200 1h bars, steadily declining -> 50 resampled 4h bars, clearly trending down
    frame = _ohlcv(200, start=300.0, step=-1.0)

    regime = higher_timeframe_regime(frame, factor=4)

    assert regime == "trending_down"


def test_higher_timeframe_regime_detects_a_clear_uptrend():
    frame = _ohlcv(200, start=100.0, step=1.0)

    regime = higher_timeframe_regime(frame, factor=4)

    assert regime == "trending_up"


def test_higher_timeframe_blocks_buy_only_on_confirmed_downtrend():
    assert higher_timeframe_blocks_buy("trending_down") is True
    assert higher_timeframe_blocks_buy("trending_up") is False
    assert higher_timeframe_blocks_buy("ranging") is False
    assert higher_timeframe_blocks_buy(None) is False  # insufficient data must not block, not treated as danger
