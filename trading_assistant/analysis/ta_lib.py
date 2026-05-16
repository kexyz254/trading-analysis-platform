import numpy as np
import pandas as pd

class TechnicalIndicators:
    def __init__(self, data):
        self.data = data
    
    def sma(self, period):
        return self.data.rolling(window=period).mean()
    
    def ema(self, period):
        return self.data.ewm(span=period, adjust=False).mean()
    
    def rsi(self, period=14):
        delta = self.data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    def macd(self, fast=12, slow=26, signal=9):
        ema_fast = self.ema(fast)
        ema_slow = self.ema(slow)
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    
    def bollinger_bands(self, period=20, std_dev=2):
        middle = self.sma(period)
        std = self.data.rolling(window=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        return upper, middle, lower
    
    def atr(self, high, low, close, period=14):
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    def stochastic(self, high, low, close, k_period=14, d_period=3):
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()
        k = 100 * (close - lowest_low) / (highest_high - lowest_low)
        d = k.rolling(window=d_period).mean()
        return k, d
    
    def cci(self, high, low, close, period=20):
        tp = (high + low + close) / 3
        sma_tp = tp.rolling(window=period).mean()
        mad = tp.rolling(window=period).apply(lambda x: abs(x - x.mean()).mean())
        return (tp - sma_tp) / (0.015 * mad)
    
    def obv(self, volume):
        obv_values = [0]
        for i in range(1, len(self.data)):
            if self.data.iloc[i] > self.data.iloc[i-1]:
                obv_values.append(obv_values[-1] + volume.iloc[i])
            elif self.data.iloc[i] < self.data.iloc[i-1]:
                obv_values.append(obv_values[-1] - volume.iloc[i])
            else:
                obv_values.append(obv_values[-1])
        return pd.Series(obv_values, index=self.data.index)
    
    def roc(self, period=12):
        return ((self.data - self.data.shift(period)) / self.data.shift(period)) * 100
    
    def keltner(self, high, low, close, period=20, multiplier=1.5):
        typical = (high + low + close) / 3
        middle = typical.rolling(window=period).mean()
        tr = (high - low).rolling(window=period).mean()
        upper = middle + (tr * multiplier)
        lower = middle - (tr * multiplier)
        return upper, middle, lower
    
    def donchian(self, period=20):
        upper = self.data.rolling(window=period).max()
        lower = self.data.rolling(window=period).min()
        middle = (upper + lower) / 2
        return upper, middle, lower
    
    def vortex(self, high, low, close, period=14):
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        vm_plus = (high - low.shift()).abs().rolling(window=period).sum()
        vm_minus = (low - high.shift()).abs().rolling(window=period).sum()
        tr_sum = tr.rolling(window=period).sum()
        vi_plus = vm_plus / tr_sum
        vi_minus = vm_minus / tr_sum
        return vi_plus, vi_minus
    
    def adx(self, high, low, close, period=14):
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        
        up_move = high - high.shift()
        down_move = low.shift() - low
        
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * plus_dm.rolling(window=period).mean() / atr
        minus_di = 100 * minus_dm.rolling(window=period).mean() / atr
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        return plus_di, minus_di, adx
