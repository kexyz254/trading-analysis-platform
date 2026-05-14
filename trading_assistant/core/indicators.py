"""Technical indicators for trading."""
import pandas as pd
import numpy as np

def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI)."""
    if len(prices) < period + 1:
        return pd.Series([np.nan] * len(prices), index=prices.index)
    
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD line, signal line, and histogram."""
    if len(prices) < slow:
        nan_series = pd.Series([np.nan] * len(prices), index=prices.index)
        return nan_series, nan_series, nan_series
    
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

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

def sma(prices, period=20):
    """simple moving average"""
    return prices.rolling(window=period).mean()

def ema(prices, period=20):
    """exponential moving average"""
    return prices.ewm(span=period, adjust=False).mean()
