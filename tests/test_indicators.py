"""Tests for technical indicators."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.core.indicators import sma, ema, stochastic_k, calculate_rsi, calculate_macd, calculate_bollinger_bands, calculate_atr, calculate_sharpe_ratio

def test_rsi_returns_between_0_and_100():
    prices = pd.Series(np.random.randn(100).cumsum() + 100)
    rsi = calculate_rsi(prices, period=14)
    assert rsi.dropna().between(0, 100).all()

def test_rsi_100_on_all_gains():
    prices = pd.Series([100 + i for i in range(20)])
    rsi = calculate_rsi(prices, period=14)
    assert rsi.iloc[14] == 100

def test_rsi_0_on_all_losses():
    prices = pd.Series([100 - i for i in range(20)])
    rsi = calculate_rsi(prices, period=14)
    assert rsi.iloc[14] == 0

def test_rsi_handles_insufficient_data():
    prices = pd.Series([100, 101, 102])
    rsi = calculate_rsi(prices, period=14)
    assert rsi.isna().all()

def test_macd_returns_three_series():
    prices = pd.Series(np.random.randn(100).cumsum() + 100)
    macd, signal, hist = calculate_macd(prices)
    assert len(macd) == len(prices)
    assert len(signal) == len(prices)
    assert len(hist) == len(prices)

def test_macd_cross_above():
    prices = pd.Series([100 + i * 0.5 for i in range(50)])
    macd, signal, hist = calculate_macd(prices)
    cross_above = (macd > signal) & (macd.shift(1) <= signal.shift(1))
    assert cross_above.any()

def test_macd_returns_nan_for_short_data():
    prices = pd.Series([100, 101, 102])
    macd, signal, hist = calculate_macd(prices)
    assert pd.isna(macd.iloc[0])  # Check for NaN

def test_bollinger_bands_return_three_series():
    """Test that Bollinger Bands returns three separate series."""
    prices = pd.Series([100 + i for i in range(100)])
    upper, middle, lower = calculate_bollinger_bands(prices)
    
    assert isinstance(upper, pd.Series)
    assert isinstance(middle, pd.Series)
    assert isinstance(lower, pd.Series)
    assert len(upper) == len(prices)
    assert len(middle) == len(prices)
    assert len(lower) == len(prices)

def test_bollinger_bands_order():
    """Test that upper >= middle >= lower after warmup period."""
    prices = pd.Series([100 + i for i in range(100)])
    upper, middle, lower = calculate_bollinger_bands(prices, period=20)
    
    valid_data_start = 20
    assert (upper.iloc[valid_data_start:] >= middle.iloc[valid_data_start:]).all()
    assert (middle.iloc[valid_data_start:] >= lower.iloc[valid_data_start:]).all()

def test_bollinger_bands_widen_with_volatility():
    """Test that higher volatility creates wider bands."""
    # Low volatility - constant prices
    low_vol = pd.Series([100] * 100)
    upper_low, middle_low, lower_low = calculate_bollinger_bands(low_vol, period=20)
    
    # High volatility - oscillating prices
    high_vol = pd.Series([100 + (i % 20) for i in range(100)])
    upper_high, middle_high, lower_high = calculate_bollinger_bands(high_vol, period=20)
    
    # Calculate average band width after warmup
    width_low = (upper_low - lower_low).iloc[20:].mean()
    width_high = (upper_high - lower_high).iloc[20:].mean()
    
    # Higher volatility should produce wider bands
    assert width_high > width_low

def test_atr_returns_positive_values():
    """Test that ATR returns positive numbers."""
    high = pd.Series([100 + i for i in range(50)])
    low = pd.Series([95 + i for i in range(50)])
    close = pd.Series([98 + i for i in range(50)])
    atr = calculate_atr(high, low, close, period=14)
    assert (atr.dropna() > 0).all()

def test_atr_increases_with_volatility():
    """Test that ATR is higher when price moves more."""
    # Low volatility
    high_low = pd.Series([100] * 50)
    low_low = pd.Series([99] * 50)
    close_low = pd.Series([100] * 50)
    
    # High volatility
    high_high = pd.Series([100, 110, 105, 115, 108] * 10)
    low_high = pd.Series([95, 100, 95, 105, 98] * 10)
    close_high = pd.Series([98, 105, 100, 110, 103] * 10)
    
    atr_low = calculate_atr(high_low, low_low, close_low, period=14)
    atr_high = calculate_atr(high_high, low_high, close_high, period=14)
    
    assert atr_high.iloc[-1] > atr_low.iloc[-1]

def test_atr_handles_insufficient_data():
    """Test that ATR returns NaN with insufficient data."""
    high = pd.Series([100, 101, 102])
    low = pd.Series([99, 100, 101])
    close = pd.Series([100, 101, 102])
    atr = calculate_atr(high, low, close, period=14)
    assert atr.isna().all()

def test_atr_returns_positive_values():
    """Test that ATR returns positive numbers."""
    high = pd.Series([100 + i for i in range(50)])
    low = pd.Series([95 + i for i in range(50)])
    close = pd.Series([98 + i for i in range(50)])
    atr = calculate_atr(high, low, close, period=14)
    assert (atr.dropna() > 0).all()

def test_atr_increases_with_volatility():
    """Test that ATR is higher when price moves more."""
    # Low volatility
    high_low = pd.Series([100] * 50)
    low_low = pd.Series([99] * 50)
    close_low = pd.Series([100] * 50)
    
    # High volatility
    high_high = pd.Series([100, 110, 105, 115, 108] * 10)
    low_high = pd.Series([95, 100, 95, 105, 98] * 10)
    close_high = pd.Series([98, 105, 100, 110, 103] * 10)
    
    atr_low = calculate_atr(high_low, low_low, close_low, period=14)
    atr_high = calculate_atr(high_high, low_high, close_high, period=14)
    
    assert atr_high.iloc[-1] > atr_low.iloc[-1]

def test_atr_handles_insufficient_data():
    """Test that ATR returns NaN with insufficient data."""
    high = pd.Series([100, 101, 102])
    low = pd.Series([99, 100, 101])
    close = pd.Series([100, 101, 102])
    atr = calculate_atr(high, low, close, period=14)
    assert atr.isna().all()

def test_sharpe_ratio_returns_float():
    """Test that Sharpe Ratio returns a float value."""
    returns = pd.Series([0.01, -0.005, 0.015, -0.01, 0.02])
    sharpe = calculate_sharpe_ratio(returns)
    assert isinstance(sharpe, float)

def test_sharpe_ratio_higher_for_better_returns():
    """Test that better returns produce higher Sharpe Ratio."""
    poor_returns = pd.Series([0.001, -0.002, 0.001, -0.003, 0.002])
    good_returns = pd.Series([0.02, 0.015, 0.025, 0.01, 0.03])
    
    sharpe_poor = calculate_sharpe_ratio(poor_returns)
    sharpe_good = calculate_sharpe_ratio(good_returns)
    
    assert sharpe_good > sharpe_poor

def test_sharpe_ratio_handles_zero_volatility():
    """Test that Sharpe Ratio handles constant returns."""
    constant_returns = pd.Series([0.01] * 10)
    sharpe = calculate_sharpe_ratio(constant_returns)
    # With zero volatility, Sharpe Ratio should be very high
    assert sharpe > 0 or pd.isna(sharpe)

def test_sma_returns_series():
    prices = pd.Series([1,2,3,4,5,6,7,8,9,10])
    result = sma(prices, period=3)
    assert len(result) == 10

def test_sma_calculation():
    prices = pd.Series([1,2,3,4,5])
    result = sma(prices, period=3)
    # first two should be nan, third = (1+2+3)/3 = 2
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == 2.0

def test_ema_returns_series():
    prices = pd.Series([1,2,3,4,5,6,7,8,9,10])
    result = ema(prices, period=3)
    assert len(result) == 10

def test_ema_differs_from_sma():
    prices = pd.Series([1,2,3,4,5,6,7,8,9,10])
    sma_result = sma(prices, period=3)
    ema_result = ema(prices, period=3)
    assert not sma_result.equals(ema_result)

def test_stochastic_returns_range():
    high = pd.Series([10 + i for i in range(50)])
    low = pd.Series([8 + i for i in range(50)])
    close = pd.Series([9 + i for i in range(50)])
    k = stochastic_k(high, low, close, period=14)
    assert (k.dropna() >= 0).all() and (k.dropna() <= 100).all()
