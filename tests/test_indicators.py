"""Tests for technical indicators."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.core.indicators import calculate_rsi, calculate_macd

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
