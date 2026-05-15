"""Tests for data fetching module."""
import pytest
import pandas as pd
from trading_assistant.data.fetcher import *

def test_generate_synthetic_prices():
    prices = generate_synthetic_prices(50)
    assert len(prices) == 50
    assert isinstance(prices, pd.Series)

def test_generate_synthetic_ohlc():
    df = generate_synthetic_ohlc(50)
    assert len(df) == 50
    assert all(col in df.columns for col in ['open', 'high', 'low', 'close', 'volume'])

def test_calculate_sma():
    prices = pd.Series([1,2,3,4,5,6,7,8,9,10])
    sma = calculate_sma(prices, 3)
    assert len(sma) == 10

def test_find_support_resistance():
    high = pd.Series([10 + i for i in range(50)])
    low = pd.Series([8 + i for i in range(50)])
    support, resistance = find_support_resistance(high, low)
    assert len(support) == 50
    assert len(resistance) == 50
