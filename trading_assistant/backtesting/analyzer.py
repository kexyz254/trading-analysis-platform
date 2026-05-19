import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime

class BacktestAnalyzer:
    def __init__(self, equity_curve: pd.Series, trades: List[Dict]):
        self.equity_curve = equity_curve
        self.trades = trades
        self.returns = equity_curve.pct_change().dropna()
        self._calculate_metrics()
    
    def _calculate_metrics(self):
        self.total_return = (self.equity_curve.iloc[-1] - self.equity_curve.iloc[0]) / self.equity_curve.iloc[0]
        self.annual_return = (1 + self.total_return) ** (252 / len(self.equity_curve)) - 1
        self.volatility = self.returns.std() * np.sqrt(252)
        self.sharpe = self.annual_return / self.volatility if self.volatility != 0 else 0
        
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        self.max_drawdown = drawdown.min()
        
        if self.trades:
            winning_trades = [t for t in self.trades if t.get('pnl', 0) > 0]
            losing_trades = [t for t in self.trades if t.get('pnl', 0) < 0]
            self.win_rate = len(winning_trades) / len(self.trades) if self.trades else 0
            self.avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
            self.avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0
            self.profit_factor = abs(sum(t['pnl'] for t in winning_trades) / sum(t['pnl'] for t in losing_trades)) if losing_trades else float('inf')
            self.expectancy = self.win_rate * self.avg_win + (1 - self.win_rate) * self.avg_loss
            self.num_trades = len(self.trades)
        else:
            self.win_rate = 0
            self.avg_win = 0
            self.avg_loss = 0
            self.profit_factor = 0
            self.expectancy = 0
            self.num_trades = 0
    
    def get_metrics(self) -> Dict:
        return {
            'total_return': self.total_return,
            'total_return_pct': self.total_return * 100,
            'annual_return': self.annual_return,
            'annual_return_pct': self.annual_return * 100,
            'volatility': self.volatility,
            'sharpe_ratio': self.sharpe,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown * 100,
            'win_rate': self.win_rate,
            'win_rate_pct': self.win_rate * 100,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'profit_factor': self.profit_factor,
            'expectancy': self.expectancy,
            'num_trades': self.num_trades
        }
    
    def monthly_returns(self) -> pd.DataFrame:
        monthly = self.equity_curve.resample('ME').last().pct_change()
        return monthly.dropna()
    
    def yearly_returns(self) -> pd.DataFrame:
        yearly = self.equity_curve.resample('YE').last().pct_change()
        return yearly.dropna()
    
    def trade_analysis(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame(self.trades)
    
    def generate_report(self) -> str:
        metrics = self.get_metrics()
        report = f"""
        ========================================
        BACKTEST ANALYSIS REPORT
        ========================================
        
        PERFORMANCE METRICS:
        --------------------
        Total Return: {metrics['total_return_pct']:.2f}%
        Annual Return: {metrics['annual_return_pct']:.2f}%
        Volatility: {metrics['volatility']:.2%}
        Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
        Max Drawdown: {metrics['max_drawdown_pct']:.2f}%
        
        TRADE STATISTICS:
        ----------------
        Total Trades: {metrics['num_trades']}
        Win Rate: {metrics['win_rate_pct']:.2f}%
        Average Win: ${metrics['avg_win']:.2f}
        Average Loss: ${metrics['avg_loss']:.2f}
        Profit Factor: {metrics['profit_factor']:.2f}
        Expectancy: ${metrics['expectancy']:.2f}
        
        ========================================
        """
        return report
