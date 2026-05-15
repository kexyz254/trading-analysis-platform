"""Concrete trading strategy implementations."""

import pandas as pd
import numpy as np
from .base import BaseStrategy, Signal
from ..core.indicators import calculate_rsi, calculate_macd

class SMACrossoverStrategy(BaseStrategy):
    """Buy when fast SMA crosses above slow SMA, sell when crosses below."""
    
    def __init__(self, fast_period=10, slow_period=30):
        super().__init__("SMA Crossover")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.last_signal = Signal.HOLD
    
    def generate_signal(self, data):
        prices = data.get('prices')
        if prices is None or len(prices) < self.slow_period:
            return Signal.HOLD
        
        fast_sma = prices.rolling(window=self.fast_period).mean()
        slow_sma = prices.rolling(window=self.slow_period).mean()
        
        if fast_sma.iloc[-1] > slow_sma.iloc[-1] and self.last_signal != Signal.BUY:
            self.last_signal = Signal.BUY
            return Signal.BUY
        elif fast_sma.iloc[-1] < slow_sma.iloc[-1] and self.last_signal != Signal.SELL:
            self.last_signal = Signal.SELL
            return Signal.SELL
        
        return Signal.HOLD

class RSIMeanReversionStrategy(BaseStrategy):
    """Buy when RSI oversold, sell when overbought."""
    
    def __init__(self, oversold=30, overbought=70, period=14):
        super().__init__("RSI Mean Reversion")
        self.oversold = oversold
        self.overbought = overbought
        self.period = period
    
    def generate_signal(self, data):
        prices = data.get('prices')
        if prices is None or len(prices) < self.period:
            return Signal.HOLD
        
        rsi = calculate_rsi(prices, self.period)
        current_rsi = rsi.iloc[-1]
        
        if current_rsi < self.oversold:
            return Signal.BUY
        elif current_rsi > self.overbought:
            return Signal.SELL
        
        return Signal.HOLD

class MACDStrategy(BaseStrategy):
    """Buy when MACD crosses above signal line, sell when crosses below."""
    
    def __init__(self, fast=12, slow=26, signal=9):
        super().__init__("MACD Strategy")
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.in_position = False
    
    def generate_signal(self, data):
        prices = data.get('prices')
        if prices is None or len(prices) < self.slow:
            return Signal.HOLD
        
        macd_line, signal_line, _ = calculate_macd(prices, self.fast, self.slow, self.signal)
        
        if macd_line.iloc[-1] > signal_line.iloc[-1] and not self.in_position:
            self.in_position = True
            return Signal.BUY
        elif macd_line.iloc[-1] < signal_line.iloc[-1] and self.in_position:
            self.in_position = False
            return Signal.SELL
        
        return Signal.HOLD

class BollingerBandsStrategy(BaseStrategy):
    """Buy when price touches lower band, sell when touches upper band."""
    
    def __init__(self, period=20, num_std=2):
        super().__init__("Bollinger Bands")
        self.period = period
        self.num_std = num_std
    
    def generate_signal(self, data):
        prices = data.get('prices')
        if prices is None or len(prices) < self.period:
            return Signal.HOLD
        
        middle = prices.rolling(window=self.period).mean()
        std = prices.rolling(window=self.period).std()
        upper = middle + (std * self.num_std)
        lower = middle - (std * self.num_std)
        
        current_price = prices.iloc[-1]
        
        if current_price <= lower.iloc[-1]:
            return Signal.BUY
        elif current_price >= upper.iloc[-1]:
            return Signal.SELL
        
        return Signal.HOLD

class GridTradingStrategy(BaseStrategy):
    """Trade within a price grid."""
    
    def __init__(self, grid_levels=10, grid_spacing=0.01):
        super().__init__("Grid Trading")
        self.grid_levels = grid_levels
        self.grid_spacing = grid_spacing
        self.positions = {}
    
    def generate_signal(self, data):
        price = data.get('price')
        if price is None:
            return Signal.HOLD
        
        # Simple grid logic - buy at lower levels, sell at higher
        grid_size = price * self.grid_spacing
        current_level = int(price / grid_size)
        
        if current_level not in self.positions:
            self.positions[current_level] = 0
        
        if current_level < self.grid_levels / 2:
            return Signal.BUY
        elif current_level > self.grid_levels / 2:
            return Signal.SELL
        
        return Signal.HOLD
