"""Concrete trading strategy implementations."""

import numpy as np
import pandas as pd

from ..core.indicators import calculate_macd, calculate_rsi
from .base import BaseStrategy, Signal


class AIStrategy(BaseStrategy):
    """Generate trading signals from a probabilistic ML model."""

    def __init__(self, model=None, feature_columns=None, buy_threshold=0.6, sell_threshold=0.4, name="AI Strategy"):
        super().__init__(name=name)
        self.model = model
        self.feature_columns = feature_columns or []
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def _prepare_features(self, data):
        if isinstance(data, pd.DataFrame):
            frame = data.copy()
        elif isinstance(data, dict):
            frame = data.get("features") or data.get("df") or pd.DataFrame(data)
        else:
            frame = pd.DataFrame(data)

        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)

        if self.feature_columns:
            return frame[self.feature_columns]
        return frame

    def generate_signal(self, data):
        features = self._prepare_features(data)
        if self.model is None or features.empty:
            return Signal.HOLD

        try:
            probabilities = self.model.predict_proba(features.iloc[[-1]])
            probability = float(probabilities[0][1])
        except AttributeError:
            prediction = self.model.predict(features.iloc[[-1]])
            if len(prediction) == 0:
                return Signal.HOLD
            value = float(prediction[0])
            if value > 0:
                return Signal.BUY
            if value < 0:
                return Signal.SELL
            return Signal.HOLD
        except (IndexError, TypeError, ValueError):
            return Signal.HOLD

        if probability >= self.buy_threshold:
            return Signal.BUY
        if probability <= self.sell_threshold:
            return Signal.SELL
        return Signal.HOLD


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
