"""Portfolio management and allocation."""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

class PortfolioManager:
    """Manage portfolio positions and risk."""
    
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions: Dict[str, Dict] = {}
        self.trade_history: List[Dict] = []
        self.equity_curve: List[float] = [initial_capital]
        
    def open_position(self, symbol: str, price: float, quantity: float, 
                      stop_loss: Optional[float] = None, 
                      take_profit: Optional[float] = None) -> bool:
        cost = price * quantity
        if cost > self.capital:
            return False
        self.positions[symbol] = {
            'price': price,
            'quantity': quantity,
            'cost': cost,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'timestamp': pd.Timestamp.now()
        }
        self.capital -= cost
        self._record_trade('BUY', symbol, price, quantity)
        return True
    
    def close_position(self, symbol: str, price: float) -> Optional[float]:
        if symbol not in self.positions:
            return None
        position = self.positions[symbol]
        proceeds = price * position['quantity']
        self.capital += proceeds
        pnl = proceeds - position['cost']
        self._record_trade('SELL', symbol, price, position['quantity'], pnl)
        del self.positions[symbol]
        self._update_equity_curve()
        return pnl
    
    def check_stop_loss_take_profit(self, symbol: str, current_price: float) -> bool:
        if symbol not in self.positions:
            return False
        position = self.positions[symbol]
        triggered = False
        if position['stop_loss'] and current_price <= position['stop_loss']:
            self.close_position(symbol, position['stop_loss'])
            triggered = True
        elif position['take_profit'] and current_price >= position['take_profit']:
            self.close_position(symbol, position['take_profit'])
            triggered = True
        return triggered
    
    def get_total_value(self, current_prices: Dict[str, float]) -> float:
        positions_value = sum(
            self.positions[sym]['quantity'] * current_prices.get(sym, 0)
            for sym in self.positions
        )
        return self.capital + positions_value
    
    def get_position_pnl(self, symbol: str, current_price: float) -> Optional[float]:
        if symbol not in self.positions:
            return None
        position = self.positions[symbol]
        return (current_price - position['price']) * position['quantity']
    
    def get_total_unrealized_pnl(self, current_prices: Dict[str, float]) -> float:
        return sum(
            self.get_position_pnl(sym, current_prices.get(sym, 0)) or 0
            for sym in self.positions
        )
    
    def _record_trade(self, action: str, symbol: str, price: float, 
                      quantity: float, pnl: Optional[float] = None):
        self.trade_history.append({
            'timestamp': pd.Timestamp.now(),
            'action': action,
            'symbol': symbol,
            'price': price,
            'quantity': quantity,
            'value': price * quantity,
            'pnl': pnl
        })
    
    def _update_equity_curve(self):
        self.equity_curve.append(self.capital)
    
    def get_performance_summary(self) -> Dict:
        if not self.trade_history:
            return {}
        trades_pnl = [t.get('pnl', 0) for t in self.trade_history if t['pnl']]
        winning_trades = [p for p in trades_pnl if p > 0]
        losing_trades = [p for p in trades_pnl if p < 0]
        return {
            'total_trades': len(trades_pnl),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(trades_pnl) if trades_pnl else 0,
            'total_pnl': sum(trades_pnl),
            'avg_win': np.mean(winning_trades) if winning_trades else 0,
            'avg_loss': np.mean(losing_trades) if losing_trades else 0,
            'profit_factor': sum(winning_trades) / abs(sum(losing_trades)) if losing_trades else float('inf')
        }
    
    def get_max_drawdown(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            if peak > 0:
                dd = (peak - value) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd
    
    def get_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        returns = []
        for i in range(1, len(self.equity_curve)):
            if self.equity_curve[i-1] > 0:
                ret = (self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1]
                returns.append(ret)
        if len(returns) < 2:
            return 0.0
        returns_array = np.array(returns)
        excess_returns = returns_array - risk_free_rate / 252
        mean_return = np.mean(excess_returns)
        std_return = np.std(excess_returns)
        if std_return == 0:
            return 0.0
        return mean_return / std_return * np.sqrt(252)
    
    def get_calmar_ratio(self) -> float:
        max_dd = self.get_max_drawdown()
        if max_dd == 0:
            return float('inf')
        if len(self.equity_curve) < 2:
            return 0.0
        total_return = (self.equity_curve[-1] - self.equity_curve[0]) / self.equity_curve[0]
        years = len(self.equity_curve) / 252
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
        return annual_return / max_dd


class RiskManager:
    """Risk management utilities."""
    
    def __init__(self, max_position_size: float = 0.1, max_daily_loss: float = 0.02):
        self.max_position_size = max_position_size
        self.max_daily_loss = max_daily_loss
        self.daily_pnl = 0
        self.current_day = None
    
    def calculate_position_size(self, capital: float, risk_per_trade: float = 0.02,
                                stop_loss_pct: float = 0.01) -> float:
        risk_amount = capital * risk_per_trade
        position_size = risk_amount / (stop_loss_pct * capital)
        return min(position_size, capital * self.max_position_size)
    
    def check_daily_loss_limit(self, today_pnl: float) -> bool:
        if self.daily_pnl + today_pnl < -self.max_daily_loss:
            return False
        return True
    
    def calculate_kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 0
        b = avg_win / abs(avg_loss)
        kelly = (win_rate * b - (1 - win_rate)) / b
        return max(0, min(kelly, 0.25))
    
    def calculate_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        return np.percentile(returns, (1 - confidence) * 100)
    
    def calculate_cvar(self, returns: pd.Series, confidence: float = 0.95) -> float:
        var = self.calculate_var(returns, confidence)
        return returns[returns <= var].mean()
