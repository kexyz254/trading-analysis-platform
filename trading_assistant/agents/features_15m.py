"""
Feature engineering for 15‑minute OHLCV bars.

This module is the 15m counterpart of agents/features.py. Every lookback
window is scaled by a factor of 4 (since 15m is ¼ of 1h) so that the
features capture similar time horizons. The output DataFrame contains the
exact same column names as the 1h pipeline, meaning the ML ensemble can
train on 15m features without any model-level changes — only the data
source differs.
"""

import pandas as pd
import numpy as np
from typing import Optional


def build_features_15m(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Build the full feature set from a 15‑minute OHLCV DataFrame.

    Parameters
    ----------
    ohlcv : pd.DataFrame
        Must contain columns 'open', 'high', 'low', 'close', 'volume'.
        Timestamp index is expected but not required for computation.

    Returns
    -------
    pd.DataFrame
        Feature columns only (no target). The column set is identical to
        FEATURE_COLUMNS from agents.features.
    """
    df = pd.DataFrame(index=ohlcv.index)

    # Price components
    close = ohlcv["close"].astype(float)
    high = ohlcv["high"].astype(float)
    low = ohlcv["low"].astype(float)
    volume = ohlcv["volume"].astype(float)

    # ---------- Returns ----------
    df["returns"] = close.pct_change()
    df["log_returns"] = np.log(close / close.shift(1))

    # ---------- Volatility (ATR-based) ----------
    # Standard ATR period is 14; for 15m we use 56 (14 * 4)
    atr_period = 56
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(atr_period).mean()
    df["atr_pct"] = df["atr"] / close

    # ---------- Moving averages ----------
    # 1h periods: fast 10, slow 30 → 15m: fast 40, slow 120
    df["sma_fast"] = close.rolling(40).mean()
    df["sma_slow"] = close.rolling(120).mean()
    df["ema_fast"] = close.ewm(span=40, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=120, adjust=False).mean()
    df["sma_fast_vs_slow"] = df["sma_fast"] / df["sma_slow"] - 1

    # ---------- RSI (14 → 56) ----------
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(56).mean()
    avg_loss = loss.rolling(56).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ---------- MACD ----------
    # 1h: fast 12, slow 26, signal 9 → 15m: fast 48, slow 104, signal 36
    ema_48 = close.ewm(span=48, adjust=False).mean()
    ema_104 = close.ewm(span=104, adjust=False).mean()
    df["macd"] = ema_48 - ema_104
    df["macd_signal"] = df["macd"].ewm(span=36, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ---------- Bollinger Bands ----------
    # 1h period 20, 2 std → 15m period 80, 2 std
    sma_80 = close.rolling(80).mean()
    std_80 = close.rolling(80).std()
    df["bb_upper"] = sma_80 + 2 * std_80
    df["bb_lower"] = sma_80 - 2 * std_80
    df["bb_width"] = df["bb_upper"] - df["bb_lower"]
    df["bb_position"] = (close - df["bb_lower"]) / df["bb_width"].replace(0, np.nan)

    # ---------- Volume features ----------
    df["volume_sma"] = volume.rolling(40).mean()
    df["volume_ratio"] = volume / df["volume_sma"]

    # ---------- ADX ----------
    # 1h period 14 → 15m period 56
    adx_period = 56
    plus_dm = high.diff().clip(lower=0)
    minus_dm = -low.diff().clip(upper=0)
    atr_adx = tr.rolling(adx_period).mean()
    plus_di = 100 * (plus_dm.rolling(adx_period).mean() / atr_adx)
    minus_di = 100 * (minus_dm.rolling(adx_period).mean() / atr_adx)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df["adx_strength"] = dx.rolling(adx_period).mean()

    # ---------- SMC / Smart Money features (recomputed on 15m) ----------
    # Swing highs/lows with N=5 (1h) → N=20 (15m) to capture similar scale
    swing_n = 20
    swing_high = high.rolling(2 * swing_n + 1, center=True).max() == high
    swing_low = low.rolling(2 * swing_n + 1, center=True).min() == low
    df["swing_high"] = swing_high.astype(float)
    df["swing_low"] = swing_low.astype(float)

    # ---------- VWAP (session-anchored, 4h anchor) ----------
    # 4h anchor in 15m bars = every 16 bars
    anchor_bars = 16
    cumulative_pv = (close * volume).rolling(anchor_bars).sum()
    cumulative_vol = volume.rolling(anchor_bars).sum()
    df["vwap"] = cumulative_pv / cumulative_vol.replace(0, np.nan)

    # ---------- Forward-fill and drop warm-up NaNs ----------
    df.ffill(inplace=True)
    df.dropna(inplace=True)

    return df