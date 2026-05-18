"""
statistical arbitrage strategy for pairs trading

looks for price divergences between correlated assets
buys underperformers, sells overperformers
"""

import numpy as np
import pandas as pd
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass

@dataclass
class TradeSignal:
    """signal from stat arb strategy"""
    action: str  # BUY, SELL, or HOLD
    confidence: float  # 0 to 1
    z_score: float
    timestamp: pd.Timestamp

class StatArbStrategy:
    """mean-reversion strategy based on z-scores"""
    
    def __init__(self, lookback=20, threshold=2.0, entry_threshold=1.5, exit_threshold=0.5):
        self.lookback = lookback
        self.threshold = threshold
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.position = 0
        self.trades = []
    
    def calculate_spread(self, series: pd.Series) -> pd.Series:
        """calculate rolling spread for a single series"""
        if len(series) < 2:
            return pd.Series(dtype=float)
        return series.diff().dropna()
    
    def calculate_zscore(self, spread: pd.Series) -> float:
        """calculate current z-score of spread"""
        if len(spread) < 2:
            return 0.0
        if spread.std() == 0:
            return 0.0
        return (spread.iloc[-1] - spread.mean()) / spread.std()
    
    def generate_signal(self, price_series: pd.Series) -> TradeSignal:
        """generate trading signal based on z-score"""
        if len(price_series) < self.lookback:
            return TradeSignal(action="HOLD", confidence=0.0, z_score=0.0, 
                             timestamp=pd.Timestamp.now())
        
        spread = self.calculate_spread(price_series[-self.lookback:])
        z_score = self.calculate_zscore(spread)
        confidence = min(1.0, abs(z_score) / self.threshold)
        
        action = "HOLD"
        if z_score > self.threshold:
            action = "SELL"
        elif z_score < -self.threshold:
            action = "BUY"
        elif abs(z_score) < self.exit_threshold and self.position != 0:
            action = "EXIT"
        
        return TradeSignal(action=action, confidence=confidence, 
                          z_score=z_score, timestamp=pd.Timestamp.now())
    
    def run_backtest(self, price_series: pd.Series) -> Dict:
        """run backtest and return performance metrics"""
        signals = []
        positions = []
        returns = []
        
        for i in range(self.lookback, len(price_series)):
            signal = self.generate_signal(price_series[:i])
            signals.append(signal)
            
            if signal.action == "BUY" and self.position == 0:
                self.position = 1
                self.trades.append({'type': 'BUY', 'price': price_series.iloc[i], 
                                   'time': signal.timestamp})
            elif signal.action == "SELL" and self.position == 0:
                self.position = -1
                self.trades.append({'type': 'SELL', 'price': price_series.iloc[i],
                                   'time': signal.timestamp})
            elif signal.action == "EXIT" and self.position != 0:
                pnl = (price_series.iloc[i] - self.trades[-1]['price']) * self.position
                returns.append(pnl)
                self.position = 0
                self.trades[-1]['exit_price'] = price_series.iloc[i]
                self.trades[-1]['pnl'] = pnl
            
            positions.append(self.position)
        
        # calculate metrics
        total_return = sum(returns) if returns else 0
        win_rate = len([r for r in returns if r > 0]) / len(returns) if returns else 0
        
        return {
            'total_return': total_return,
            'num_trades': len(self.trades),
            'win_rate': win_rate,
            'signals': signals,
            'positions': positions
        }
    
    def pairs_trading(self, series_a: pd.Series, series_b: pd.Series) -> TradeSignal:
        """pairs trading between two correlated assets"""
        if len(series_a) < self.lookback or len(series_b) < self.lookback:
            return TradeSignal(action="HOLD", confidence=0.0, z_score=0.0,
                             timestamp=pd.Timestamp.now())
        
        # calculate spread between the two
        hedge_ratio = series_a[-self.lookback:].std() / series_b[-self.lookback:].std()
        spread = series_a - hedge_ratio * series_b
        spread_change = spread.diff().dropna()
        
        z_score = self.calculate_zscore(spread_change)
        confidence = min(1.0, abs(z_score) / self.threshold)
        
        action = "HOLD"
        if z_score > self.threshold:
            action = "SELL_A_BUY_B"  # short A, long B
        elif z_score < -self.threshold:
            action = "BUY_A_SELL_B"  # long A, short B
        
        return TradeSignal(action=action, confidence=confidence,
                          z_score=z_score, timestamp=pd.Timestamp.now())
    
    def get_summary(self) -> str:
        """get human-readable strategy summary"""
        summary = f"""
        Stat Arb Strategy Summary
        -------------------------
        Lookback period: {self.lookback}
        Entry threshold: ±{self.entry_threshold}
        Exit threshold: ±{self.exit_threshold}
        Total trades executed: {len(self.trades)}
        """
        return summary

def test_strategy():
    """simple test to verify strategy works"""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=500, freq='D')
    prices = 100 + np.cumsum(np.random.randn(500) * 0.5)
    price_series = pd.Series(prices, index=dates)
    
    strategy = StatArbStrategy(lookback=20, threshold=2.0)
    result = strategy.run_backtest(price_series)
    
    print(f"Total return: {result['total_return']:.2f}")
    print(f"Win rate: {result['win_rate']:.2%}")
    print(f"Trades: {result['num_trades']}")
    
    return result

if __name__ == "__main__":
    test_strategy()