"""Advanced backtest analysis and reporting."""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import matplotlib.pyplot as plt

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
            self.avg_trade_duration = np.mean([t.get('duration', 0) for t in self.trades]) if self.trades else 0
        else:
            self.win_rate = 0
            self.avg_win = 0
            self.avg_loss = 0
            self.profit_factor = 0
            self.expectancy = 0
            self.num_trades = 0
            self.avg_trade_duration = 0
    
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
            'num_trades': self.num_trades,
            'avg_trade_duration': self.avg_trade_duration
        }
    
    def monthly_returns(self) -> pd.DataFrame:
        monthly = self.equity_curve.resample('M').last().pct_change()
        return monthly.dropna()
    
    def yearly_returns(self) -> pd.DataFrame:
        yearly = self.equity_curve.resample('Y').last().pct_change()
        return yearly.dropna()
    
    def rolling_sharpe(self, window: int = 252) -> pd.Series:
        rolling_returns = self.returns.rolling(window)
        return rolling_returns.mean() / rolling_returns.std() * np.sqrt(252)
    
    def rolling_max_drawdown(self, window: int = 252) -> pd.Series:
        rolling_max = self.equity_curve.rolling(window).max()
        drawdown = (self.equity_curve - rolling_max) / rolling_max
        return drawdown
    
    def trade_analysis(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.trades)
        
        df['hour'] = pd.to_datetime(df['timestamp']).dt.hour
        df['day_of_week'] = pd.to_datetime(df['timestamp']).dt.dayofweek
        df['month'] = pd.to_datetime(df['timestamp']).dt.month
        
        return df
    
    def plot_equity_curve(self):
        fig, axes = plt.subplots(3, 1, figsize=(12, 10))
        
        axes[0].plot(self.equity_curve.index, self.equity_curve.values, color='blue')
        axes[0].set_title('Equity Curve')
        axes[0].set_ylabel('Portfolio Value')
        axes[0].grid(True)
        
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        axes[1].fill_between(drawdown.index, 0, drawdown.values, color='red', alpha=0.3)
        axes[1].set_title('Drawdown')
        axes[1].set_ylabel('Drawdown %')
        axes[1].grid(True)
        
        axes[2].bar(self.returns.index, self.returns.values, color='green', alpha=0.5)
        axes[2].set_title('Daily Returns')
        axes[2].set_ylabel('Return')
        axes[2].grid(True)
        
        plt.tight_layout()
        return fig
    
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
