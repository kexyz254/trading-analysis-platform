import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pytest
import pandas as pd
import numpy as np

@pytest.fixture
def sample_prices():
    """Generate sample price series for testing."""
    np.random.seed(42)
    prices = pd.Series(np.random.randn(100).cumsum() + 100)
    return prices

@pytest.fixture
def sample_ohlc():
    """Generate sample OHLC data for testing."""
    np.random.seed(42)
    n = 100
    close = pd.Series(np.random.randn(n).cumsum() + 100)
    high = close + np.random.uniform(0, 2, n)
    low = close - np.random.uniform(0, 2, n)
    open_price = close - np.random.uniform(-1, 1, n)
    volume = np.random.randint(1000, 10000, n)
    return open_price, high, low, close, volume
