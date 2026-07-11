"""Shared feature engineering for both training (LearningAgent) and live
prediction (Orchestrator -> PredictionAgent).

This is the single source of truth for "raw OHLCV -> model input". If
training and live prediction each built features independently, they could
silently drift apart (train/serve skew) and a promoted model would break -
or worse, run - on a different feature distribution than it was trained on.

All features are deliberately normalized (ratios, percentages, bounded
oscillators, z-scores) rather than raw price levels or unbounded cumulative
sums. Feeding a model raw SMA/Bollinger/OBV values in absolute units is a
classic mistake: BTC at $20k and BTC at $100k are the same *pattern* but
wildly different *numbers* (and OBV grows without bound over time), so a
model trained on raw levels implicitly overfits to the current regime
instead of learning anything that transfers across time.

This uses the full TechnicalIndicators library (SMA/EMA/RSI/MACD/Bollinger/
ATR/Stochastic/CCI/ADX/Vortex/Donchian/Keltner/OBV) plus additional
OHLCV-derived momentum, volatility, volume, candle-shape, VWAP-deviation,
and session (hour-of-day) signals - deliberately the full space of what's
trainable from Binance's REST OHLCV history.

Order-book/trade-flow signals (bid-ask imbalance, aggressor flow) are NOT
included: Binance's REST API only exposes the *current* order book and
recent trades, with no historical snapshot endpoint, so those signals can't
be reconstructed for historical training bars without data leakage. They
could become a live-only overlay later, once the system has accumulated its
own historical order-book log over time.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from trading_assistant.analysis.smart_money_concepts import (
    bearish_order_block_zones,
    bullish_order_block_zones,
    compute_market_structure,
)
from trading_assistant.analysis.ta_lib import TechnicalIndicators

FEATURE_COLUMNS = [
    # trend / moving averages
    "price_vs_sma_fast",
    "price_vs_sma_slow",
    "sma_fast_vs_slow",
    # momentum oscillators
    "rsi_normalized",
    "stochastic_k",
    "stochastic_d",
    "cci_normalized",
    "macd_hist_pct",
    "roc_pct",
    # trend strength / direction
    "adx_strength",
    "adx_directional",
    "vortex_diff",
    # volatility / bands
    "bb_position",
    "bb_width_pct",
    "atr_pct",
    "donchian_position",
    "keltner_position",
    # volume
    "volume_change",
    "relative_volume",
    "obv_zscore",
    # returns / momentum lookbacks
    "return_1",
    "return_3",
    "return_5",
    "return_10",
    "return_20",
    "volatility_10",
    # candle shape
    "candle_body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    # price-level deviation
    "price_vs_vwap",
    # session / cyclical
    "hour_sin",
    "hour_cos",
    # Smart Money Concepts / market structure (see
    # trading_assistant.analysis.smart_money_concepts) - signed/bounded
    # like adx_directional and vortex_diff above, not one-hot, for the
    # same reason: a directional +1/-1/0 scale is directly comparable
    # across symbols and price regimes.
    "smc_structure_state",
    "smc_bos",
    "smc_mss",
    "smc_in_bullish_ob_zone",
    "smc_in_bearish_ob_zone",
]


def build_features(
    frame: pd.DataFrame,
    sma_fast_period: int = 10,
    sma_slow_period: int = 30,
    rsi_period: int = 14,
) -> pd.DataFrame:
    """Engineer FEATURE_COLUMNS from raw OHLCV. Rows with insufficient
    history for the slowest indicator will have NaN features - callers
    should dropna() before training, or rely on a long enough warm-up
    window (as MarketMonitorAgent's default history_size provides) for
    live single-row prediction."""
    result = frame.copy()
    close = result["close"]
    high = result["high"]
    low = result["low"]
    volume = result["volume"]
    indicators = TechnicalIndicators(close)

    sma_fast = indicators.sma(sma_fast_period)
    sma_slow = indicators.sma(sma_slow_period)
    rsi = indicators.rsi(rsi_period)
    _, _, macd_hist = indicators.macd()
    bb_upper, bb_mid, bb_lower = indicators.bollinger_bands()
    atr = indicators.atr(high, low, close)
    stoch_k, stoch_d = indicators.stochastic(high, low, close)
    cci = indicators.cci(high, low, close)
    obv = indicators.obv(volume)
    roc = indicators.roc()
    keltner_upper, _, keltner_lower = indicators.keltner(high, low, close)
    donchian_upper, _, donchian_lower = indicators.donchian()
    vortex_plus, vortex_minus = indicators.vortex(high, low, close)
    plus_di, minus_di, adx = indicators.adx(high, low, close)

    result["price_vs_sma_fast"] = close / sma_fast - 1
    result["price_vs_sma_slow"] = close / sma_slow - 1
    result["sma_fast_vs_slow"] = sma_fast / sma_slow - 1

    result["rsi_normalized"] = rsi / 100.0
    result["stochastic_k"] = stoch_k / 100.0
    result["stochastic_d"] = stoch_d / 100.0
    result["cci_normalized"] = cci / 100.0
    result["macd_hist_pct"] = macd_hist / close
    result["roc_pct"] = roc / 100.0

    result["adx_strength"] = adx / 100.0
    result["adx_directional"] = (plus_di - minus_di) / 100.0
    result["vortex_diff"] = vortex_plus - vortex_minus

    result["bb_position"] = (close - bb_lower) / (bb_upper - bb_lower)
    result["bb_width_pct"] = (bb_upper - bb_lower) / bb_mid
    result["atr_pct"] = atr / close
    result["donchian_position"] = (close - donchian_lower) / (donchian_upper - donchian_lower)
    result["keltner_position"] = (close - keltner_lower) / (keltner_upper - keltner_lower)

    result["volume_change"] = volume.pct_change()
    result["relative_volume"] = volume / volume.rolling(20).mean()
    obv_mean = obv.rolling(20).mean()
    obv_std = obv.rolling(20).std()
    result["obv_zscore"] = (obv - obv_mean) / obv_std

    result["return_1"] = close.pct_change(1)
    result["return_3"] = close.pct_change(3)
    result["return_5"] = close.pct_change(5)
    result["return_10"] = close.pct_change(10)
    result["return_20"] = close.pct_change(20)
    result["volatility_10"] = result["return_1"].rolling(10).std()

    candle_range = (high - low).replace(0, np.nan)
    body_high = result[["open", "close"]].max(axis=1)
    body_low = result[["open", "close"]].min(axis=1)
    result["candle_body_ratio"] = (close - result["open"]) / candle_range
    result["upper_wick_ratio"] = (high - body_high) / candle_range
    result["lower_wick_ratio"] = (body_low - low) / candle_range

    typical_price = (high + low + close) / 3
    vwap_20 = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum()
    result["price_vs_vwap"] = close / vwap_20 - 1

    hour = pd.to_datetime(result["timestamp"]).dt.hour
    result["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    result["hour_cos"] = np.cos(2 * np.pi * hour / 24)

    structure = compute_market_structure(high, low, close)
    result["smc_structure_state"] = structure["structure_state"].map(
        {"bullish": 1.0, "bearish": -1.0, "ranging": 0.0}
    )
    result["smc_bos"] = structure["bos"].astype(float)
    result["smc_mss"] = structure["mss"].astype(float)

    bullish_ob = bullish_order_block_zones(result["open"], high, low, close, atr)
    bearish_ob = bearish_order_block_zones(result["open"], high, low, close, atr)
    # Compared against the PREVIOUS bar's zone bounds, not this same row's -
    # a zone that's actively being retested this bar is also mitigated
    # (cleared) by this same bar's own price action in the zone-tracking
    # output above, so checking this row's own zone_low/zone_high can
    # never catch the retest that just consumed it. The previous row's
    # bounds reflect the zone as it stood BEFORE this bar's action, which
    # is what "does this bar's close land inside a zone established
    # earlier" actually means.
    result["smc_in_bullish_ob_zone"] = (
        (close >= bullish_ob["zone_low"].shift(1)) & (close <= bullish_ob["zone_high"].shift(1))
    ).astype(float)
    result["smc_in_bearish_ob_zone"] = (
        (close >= bearish_ob["zone_low"].shift(1)) & (close <= bearish_ob["zone_high"].shift(1))
    ).astype(float)

    result[FEATURE_COLUMNS] = result[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)

    return result


def label_next_bar_direction(frame: pd.DataFrame) -> pd.Series:
    """1 if the next bar closes higher than the current bar, else 0."""
    return (frame["close"].shift(-1) > frame["close"]).astype(int)


def classify_regime(trend_strength: float, adx_strength: float, adx_trend_threshold: float = 0.25) -> str:
    """Classify market regime from already-computed sma_fast_vs_slow
    (direction) and adx/100 (trend strength). ADX > ~25 (0.25 here) is the
    standard technical-analysis threshold for "the market is trending" -
    below that, direction is noise and the market is better described as
    ranging regardless of which way trend_strength happens to point.

    Single source of truth shared by MarketMonitorAgent (live regime
    logging) and StrategyManager (regime-tagging walk-forward folds) - two
    independent regime definitions would mean a strategy validated for
    "trending_up" per one classifier could get deployed under a different
    live definition of the same label.
    """
    if pd.isna(trend_strength) or pd.isna(adx_strength):
        return "ranging"
    if adx_strength < adx_trend_threshold:
        return "ranging"
    return "trending_up" if trend_strength > 0 else "trending_down"


def classify_market_condition(
    atr_pct: float,
    relative_volume: float,
    panic_atr_threshold: float = 0.08,
    illiquid_volume_threshold: float = 0.3,
) -> Optional[str]:
    """'panic' | 'illiquid' | None - an OVERLAY on top of classify_regime's
    trend/range taxonomy, not a replacement for it. Trend/range describe
    DIRECTION; this describes whether current conditions are trustworthy
    enough to trade AT ALL regardless of direction, and is checked at the
    live decision level (Orchestrator sits out entirely on either) rather
    than folded into the tournament's regime taxonomy - walk-forward
    folds are already validated as trending_up/trending_down/ranging, and
    changing that taxonomy would invalidate every existing stability_score
    history rather than add a genuinely new signal.

    'panic': ATR (as a fraction of price) has spiked far beyond normal
    single-bar movement - 8% is a deliberately high bar, well past
    ordinary crypto volatility, chosen so this only fires on a genuine
    dislocation, not routine noise (see market_data.integrity's
    extreme-move guard, which uses a similar philosophy for a different
    purpose - flagging a DATA anomaly rather than a market CONDITION).

    'illiquid': volume has fallen far below its own recent average (30%
    of the 20-bar mean) - thin trading where a fill is more likely to
    move the market or fail to reflect a fair price, independent of the
    bid-ask spread guard already checked immediately before a real order.
    """
    if pd.isna(atr_pct) or pd.isna(relative_volume):
        return None
    if atr_pct >= panic_atr_threshold:
        return "panic"
    if relative_volume <= illiquid_volume_threshold:
        return "illiquid"
    return None
