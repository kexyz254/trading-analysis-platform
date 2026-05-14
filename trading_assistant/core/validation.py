"""Validation utilities for trading data."""
import pandas as pd

def validate_ohlc(df):
    """Validate OHLC dataframe has required columns and correct values."""
    required = ['open', 'high', 'low', 'close', 'volume']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    
    # Validate OHLC logic
    if (df['high'] < df['low']).any():
        raise ValueError("High is less than low")
    if (df['high'] < df['open']).any() or (df['high'] < df['close']).any():
        raise ValueError("High not highest")
    if (df['low'] > df['open']).any() or (df['low'] > df['close']).any():
        raise ValueError("Low not lowest")
    
    return True
