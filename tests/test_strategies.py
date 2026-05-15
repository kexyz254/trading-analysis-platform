"""Tests for trading strategies."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import *

def test_sma_crossover_strategy():
    strategy = SMACrossoverStrategy(3, 6)
    prices = pd.Series([100, 101, 102, 103, 104, 105, 106])
    
    for i in range(len(prices)):
        signal = strategy.generate_signal({'prices': prices[:i+1], 'price': prices.iloc[i]})
        assert signal in [Signal.BUY, Signal.SELL, Signal.HOLD]

def test_rsi_strategy():
    strategy = RSIMeanReversionStrategy(30, 70)
    prices = pd.Series([100] * 20 + [80] * 10)
    
    for i in range(len(prices)):
        signal = strategy.generate_signal({'prices': prices[:i+1], 'price': prices.iloc[i]})
        assert signal in [Signal.BUY, Signal.SELL, Signal.HOLD]

def test_macd_strategy():
    strategy = MACDStrategy(5, 13, 4)
    prices = pd.Series([100 + i for i in range(50)])
    
    for i in range(len(prices)):
        signal = strategy.generate_signal({'prices': prices[:i+1], 'price': prices.iloc[i]})
        assert signal in [Signal.BUY, Signal.SELL, Signal.HOLD]
