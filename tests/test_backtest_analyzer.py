import pytest
import pandas as pd
import numpy as np
from trading_assistant.backtesting.analyzer import BacktestAnalyzer

def test_analyzer_metrics():
    equity = pd.Series([100, 101, 102, 101, 103, 104])
    trades = [{'pnl': 1}, {'pnl': -1}, {'pnl': 2}]
    analyzer = BacktestAnalyzer(equity, trades)
    metrics = analyzer.get_metrics()
    assert 'total_return' in metrics
    assert 'sharpe_ratio' in metrics

def test_analyzer_monthly_returns():
    dates = pd.date_range('2024-01-01', periods=365, freq='D')
    equity = pd.Series(np.random.randn(365).cumsum() + 100, index=dates)
    analyzer = BacktestAnalyzer(equity, [])
    monthly = analyzer.monthly_returns()
    assert len(monthly) > 0

def test_analyzer_generate_report():
    equity = pd.Series([100, 101, 102, 101, 103, 104])
    trades = [{'pnl': 1, 'timestamp': '2024-01-01'},
              {'pnl': -1, 'timestamp': '2024-01-02'}]
    analyzer = BacktestAnalyzer(equity, trades)
    report = analyzer.generate_report()
    assert 'BACKTEST ANALYSIS REPORT' in report
