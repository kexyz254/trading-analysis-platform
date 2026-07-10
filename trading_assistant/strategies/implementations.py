"""Concrete trading strategy implementations."""

from typing import Optional

import numpy as np
import pandas as pd

from ..analysis.candlestick_patterns import (
    bearish_engulfing,
    bearish_harami,
    bullish_engulfing,
    bullish_harami,
    dragonfly_doji,
    evening_star,
    gravestone_doji,
    hammer,
    morning_star,
    shooting_star,
)
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
        # The model's raw "probability of up" for the most recent
        # generate_signal() call - distinct from PredictProbaAdapter's
        # last_confidence (an ensemble agreement metric). This is the actual
        # number the buy/sell thresholds are applied to, and without
        # exposing it nothing downstream (journal, dashboard) can show HOW
        # CLOSE a HOLD was to actually firing a trade.
        self.last_probability_up: Optional[float] = None

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
        # Reset before attempting: if this call falls through to HOLD via an
        # early return below, a stale value from a previous successful call
        # must not linger and get journaled as if it were current.
        self.last_probability_up = None

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

        self.last_probability_up = probability
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

class TimeSeriesMomentumStrategy(BaseStrategy):
    """Trailing N-bar momentum - the mechanism most consistently validated
    for crypto specifically in the academic literature. Han, Kang & Ryu
    (2023, "Time-Series and Cross-Sectional Momentum in the Cryptocurrency
    Market") find time-series momentum survives realistic transaction costs
    where cross-sectional momentum mostly does not once fairly assessed.

    Distinct in MECHANISM from SMACrossoverStrategy, not just a
    reparameterization of it: this reacts directly to the sign and
    magnitude of one fixed trailing-window return, not to where two
    lagging moving averages happen to cross - a different lag profile and
    a different failure mode (SMA crossover whipsaws on a slow, gradual
    reversal; this whipsaws on a short, sharp round trip within the
    lookback window instead).

    min_abs_return is a deliberate dead-zone around a near-flat trailing
    return: the same literature's own caveat - many momentum portfolios'
    statistically significant returns turn insignificant after realistic
    transaction costs - means trading every small sign flip would bleed
    exactly the costs that caveat warns about. Withholding a signal near
    zero is the mechanical equivalent of only trading when momentum is
    unambiguous.
    """

    def __init__(self, lookback: int = 20, min_abs_return: float = 0.02):
        super().__init__("Time-Series Momentum")
        self.lookback = lookback
        self.min_abs_return = min_abs_return

    def generate_signal(self, data):
        prices = data.get('prices')
        if prices is None or len(prices) < self.lookback + 1:
            return Signal.HOLD

        anchor = prices.iloc[-1 - self.lookback]
        if pd.isna(anchor) or anchor == 0:
            return Signal.HOLD
        trailing_return = prices.iloc[-1] / anchor - 1
        if pd.isna(trailing_return):
            return Signal.HOLD

        if trailing_return >= self.min_abs_return:
            return Signal.BUY
        if trailing_return <= -self.min_abs_return:
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


class CandlestickPatternStrategy(BaseStrategy):
    """Reversal trading from Japanese candlestick patterns, gated by trend
    context - codifying The Candlestick Trading Bible's single most-repeated
    rule: "you can't trade any candlestick pattern alone, you will need
    other technical tools to confirm your entries." A bullish reversal
    pattern (hammer, bullish engulfing, bullish harami, morning star,
    dragonfly doji) only signals BUY when the recent trend has actually
    been down - trading a hammer mid-uptrend isn't a reversal signal at
    all, per the book's own examples (every illustrated hammer/engulfing
    trade occurs at the end of an established move, never in the middle of
    one). The trend filter here (price vs. a declining/rising rolling SMA)
    is a mechanical stand-in for the book's hand-drawn support/resistance
    and trendline confluence, which this system can't yet compute -
    deliberately simple rather than a false-precision reimplementation of
    subjective chart-drawing.

    This is a strategy CANDIDATE like every other one in
    StrategyManager.default_candidates() - it earns a live decision only by
    clearing the same walk-forward promotion bar (min Sharpe, min folds,
    min %% profitable) the tournament applies to everything else. Adding it
    here does not mean it trades; it means it gets to compete."""

    def __init__(self, trend_period: int = 20):
        super().__init__("Candlestick Patterns")
        self.trend_period = trend_period

    def generate_signal(self, data):
        open_, high, low, close = data.get("open"), data.get("high"), data.get("low"), data.get("close")
        if open_ is None or high is None or low is None or close is None:
            return Signal.HOLD
        # Need enough history for the trend SMA plus morning/evening star's
        # 2-bar lookback, or every early bar would be a spurious NaN-driven signal.
        if len(close) < self.trend_period + 3:
            return Signal.HOLD

        # Trend context is judged as of the bar BEFORE the candidate pattern
        # candle, not contaminated by it - the whole point of a reversal
        # pattern is that its own close breaks the prior trend, so including
        # it in the trend judgment would be circular (a strong bearish
        # reversal candle can single-handedly drag a short SMA window below
        # the "uptrend" it's meant to be reversing).
        sma = close.rolling(self.trend_period).mean()
        if len(close) < self.trend_period + 4:
            return Signal.HOLD
        reference_close, reference_sma = close.iloc[-2], sma.iloc[-2]
        prior_sma = sma.iloc[-2 - self.trend_period]
        if pd.isna(reference_sma) or pd.isna(prior_sma):
            return Signal.HOLD

        downtrend_context = reference_close < reference_sma and reference_sma < prior_sma
        uptrend_context = reference_close > reference_sma and reference_sma > prior_sma

        if downtrend_context:
            bullish_reversal = (
                bullish_engulfing(open_, high, low, close).iloc[-1]
                or hammer(open_, high, low, close).iloc[-1]
                or dragonfly_doji(open_, high, low, close).iloc[-1]
                or bullish_harami(open_, high, low, close).iloc[-1]
                or morning_star(open_, high, low, close).iloc[-1]
            )
            if bullish_reversal:
                return Signal.BUY

        if uptrend_context:
            bearish_reversal = (
                bearish_engulfing(open_, high, low, close).iloc[-1]
                or shooting_star(open_, high, low, close).iloc[-1]
                or gravestone_doji(open_, high, low, close).iloc[-1]
                or bearish_harami(open_, high, low, close).iloc[-1]
                or evening_star(open_, high, low, close).iloc[-1]
            )
            if bearish_reversal:
                return Signal.SELL

        return Signal.HOLD
