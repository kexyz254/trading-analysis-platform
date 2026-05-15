"""Data fetching utilities for market data."""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def generate_synthetic_prices(days=100, start_price=100, volatility=0.02):
    """Generate synthetic price data for testing."""
    np.random.seed(42)
    returns = np.random.normal(0, volatility, days)
    prices = start_price * (1 + returns).cumprod()
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')
    return pd.Series(prices, index=dates)

def generate_synthetic_ohlc(days=100, start_price=100, volatility=0.02):
    """Generate synthetic OHLC data."""
    close = generate_synthetic_prices(days, start_price, volatility)
    high = close * (1 + np.random.uniform(0, 0.02, days))
    low = close * (1 - np.random.uniform(0, 0.02, days))
    open_price = (high + low) / 2
    volume = np.random.randint(1000, 100000, days)
    
    df = pd.DataFrame({
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=close.index)
    return df

def calculate_sma(prices, period):
    """Simple moving average."""
    return prices.rolling(window=period).mean()

def calculate_ema(prices, period):
    """Exponential moving average."""
    return prices.ewm(span=period, adjust=False).mean()

def find_support_resistance(high, low, window=20):
    """Find support and resistance levels."""
    resistance = high.rolling(window=window).max()
    support = low.rolling(window=window).min()
    return support, resistance
