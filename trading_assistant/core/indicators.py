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

def obv(close, volume):
    """On-Balance Volume indicator."""
    obv_values = [0]
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            obv_values.append(obv_values[-1] + volume.iloc[i])
        elif close.iloc[i] < close.iloc[i-1]:
            obv_values.append(obv_values[-1] - volume.iloc[i])
        else:
            obv_values.append(obv_values[-1])
    return pd.Series(obv_values, index=close.index)

def roc(prices, period=12):
    """Rate of Change indicator."""
    return ((prices - prices.shift(period)) / prices.shift(period)) * 100

def keltner_channel(high, low, close, period=20, multiplier=1.5):
    """Keltner Channel indicator."""
    typical_price = (high + low + close) / 3
    middle = typical_price.rolling(window=period).mean()
    true_range = (high - low).rolling(window=period).mean()
    upper = middle + (true_range * multiplier)
    lower = middle - (true_range * multiplier)
    return upper, middle, lower

def donchian_channel(high, low, period=20):
    """Donchian Channel indicator."""
    upper = high.rolling(window=period).max()
    lower = low.rolling(window=period).min()
    middle = (upper + lower) / 2
    return upper, middle, lower

def ichimoku_cloud(high, low, close, conversion=9, base=26, lagging=52):
    """Calculate Ichimoku Cloud components."""
    conversion_line = (high.rolling(window=conversion).max() + 
                       low.rolling(window=conversion).min()) / 2
    base_line = (high.rolling(window=base).max() + 
                 low.rolling(window=base).min()) / 2
    leading_span_a = ((conversion_line + base_line) / 2).shift(base)
    leading_span_b = ((high.rolling(window=lagging).max() + 
                       low.rolling(window=lagging).min()) / 2).shift(base)
    lagging_span = close.shift(-base)
    
    return conversion_line, base_line, leading_span_a, leading_span_b, lagging_span

def vortex_indicator(high, low, close, period=14):
    """Calculate Vortex Indicator."""
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    vm_plus = (high - low.shift()).abs().rolling(window=period).sum()
    vm_minus = (low - high.shift()).abs().rolling(window=period).sum()
    tr_sum = tr.rolling(window=period).sum()
    
    vi_plus = vm_plus / tr_sum
    vi_minus = vm_minus / tr_sum
    
    return vi_plus, vi_minus

def ultimate_oscillator(high, low, close, period1=7, period2=14, period3=28):
    """Calculate Ultimate Oscillator."""
    bp = close - pd.concat([low, close.shift()], axis=1).min(axis=1)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    avg1 = bp.rolling(window=period1).sum() / tr.rolling(window=period1).sum()
    avg2 = bp.rolling(window=period2).sum() / tr.rolling(window=period2).sum()
    avg3 = bp.rolling(window=period3).sum() / tr.rolling(window=period3).sum()
    
    uo = 100 * (4 * avg1 + 2 * avg2 + avg3) / 7
    return uo

def fibonacci_retracement(high, low, levels=[0.236, 0.382, 0.5, 0.618, 0.786]):
    """Calculate Fibonacci retracement levels."""
    diff = high - low
    return {level: high - (diff * level) for level in levels}
