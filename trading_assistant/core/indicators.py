"""Technical indicators for trading."""
import pandas as pd
import numpy as np


def calculate_bollinger_bands(prices, period=20, num_std=2):
    """Calculate Bollinger Bands for price data."""
    middle_band = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper_band = middle_band + (std * num_std)
    lower_band = middle_band - (std * num_std)
    return upper_band, middle_band, lower_band

def calculate_atr(high, low, close, period=14):
    """Calculate Average True Range (ATR) for volatility measurement."""
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    
    return atr

def calculate_sharpe_ratio(returns, risk_free_rate=0.02, periods_per_year=252):
    """Calculate Sharpe Ratio for risk-adjusted returns."""
    excess_returns = returns - (risk_free_rate / periods_per_year)
    sharpe = excess_returns.mean() / excess_returns.std() * (periods_per_year ** 0.5)
    return sharpe

def ema(prices, period=20):
    """exponential moving average"""
    return prices.ewm(span=period, adjust=False).mean()

def williams_r(high, low, close, period=14):
    """williams %R"""
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    return -100 * (highest_high - close) / (highest_high - lowest_low)

def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI)."""
    if len(prices) < period + 1:
        return pd.Series([np.nan] * len(prices), index=prices.index)
    
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    # Fix: handle case when both gain and loss are zero
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # When gain and loss are both zero, rs is NaN, set rsi to 50
    rsi = rsi.fillna(50)
    
    return rsi

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD line, signal line, and histogram."""
    # Handle empty or None input
    if prices is None or len(prices) == 0:
        empty = pd.Series(dtype=float)
        return empty, empty, empty
    
    if len(prices) < slow:
        nan_series = pd.Series([np.nan] * len(prices), index=prices.index)
        return nan_series, nan_series, nan_series
    
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
from functools import lru_cache
import pandas as pd

@lru_cache(maxsize=128)
def _sma_cached(prices_tuple, period):
    """Cached version of SMA calculation."""
    prices = pd.Series(prices_tuple)
    return prices.rolling(window=period).mean()

def sma(prices, period=20):
    """Simple moving average with caching."""
    # Convert Series to tuple for hashing
    prices_tuple = tuple(prices.values)
    return _sma_cached(prices_tuple, period)

def stochastic_k(high, low, close, period=14):
    """Optimized stochastic oscillator %K."""
    # Use rolling min/max which is already vectorized
    lowest_low = low.rolling(window=period).min()
    highest_high = high.rolling(window=period).max()
    # Avoid division by zero
    denominator = highest_high - lowest_low
    k = 100 * (close - lowest_low) / denominator
    # Handle division by zero
    k = k.where(denominator != 0, 50)
    return k
