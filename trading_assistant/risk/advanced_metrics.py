"""Advanced risk metrics for portfolio analysis."""

import numpy as np
import pandas as pd
from scipy import stats
from typing import List, Dict, Tuple, Optional

class AdvancedRiskMetrics:
    def __init__(self, returns: pd.Series):
        self.returns = returns.dropna()
        self.n = len(self.returns)
    
    def historical_var(self, confidence: float = 0.95) -> float:
        return np.percentile(self.returns, (1 - confidence) * 100)
    
    def historical_cvar(self, confidence: float = 0.95) -> float:
        var = self.historical_var(confidence)
        return self.returns[self.returns <= var].mean()
    
    def parametric_var(self, confidence: float = 0.95) -> float:
        mean = self.returns.mean()
        std = self.returns.std()
        z_score = stats.norm.ppf(1 - confidence)
        return mean + z_score * std
    
    def parametric_cvar(self, confidence: float = 0.95) -> float:
        mean = self.returns.mean()
        std = self.returns.std()
        alpha = 1 - confidence
        z_alpha = stats.norm.ppf(alpha)
        pdf_z = stats.norm.pdf(z_alpha)
        cvar = mean - std * (pdf_z / alpha)
        return cvar
    
    def cornish_fisher_var(self, confidence: float = 0.95) -> float:
        mean = self.returns.mean()
        std = self.returns.std()
        skew = self.returns.skew()
        kurt = self.returns.kurtosis()
        z_score = stats.norm.ppf(1 - confidence)
        z_cf = z_score + (z_score**2 - 1) * skew / 6
        z_cf += (z_score**3 - 3 * z_score) * kurt / 24
        z_cf -= (2 * z_score**3 - 5 * z_score) * skew**2 / 36
        return mean + z_cf * std
    
    def expected_shortfall(self, confidence: float = 0.95) -> float:
        return self.historical_cvar(confidence)
    
    def maximum_drawdown(self) -> float:
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return drawdown.min()
    
    def average_drawdown(self) -> float:
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return drawdown[drawdown < 0].mean()
    
    def drawdown_duration(self) -> Dict:
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        
        in_drawdown = False
        current_duration = 0
        durations = []
        
        for dd in drawdown:
            if dd < 0 and not in_drawdown:
                in_drawdown = True
                current_duration = 1
            elif dd < 0 and in_drawdown:
                current_duration += 1
            elif dd >= 0 and in_drawdown:
                in_drawdown = False
                durations.append(current_duration)
                current_duration = 0
        
        return {
            'max_duration': max(durations) if durations else 0,
            'avg_duration': np.mean(durations) if durations else 0,
            'num_events': len(durations)
        }
    
    def calmar_ratio(self, periods_per_year: int = 252) -> float:
        annual_return = self.returns.mean() * periods_per_year
        max_dd = self.maximum_drawdown()
        return annual_return / abs(max_dd) if max_dd != 0 else float('inf')
    
    def sterling_ratio(self, periods_per_year: int = 252) -> float:
        annual_return = self.returns.mean() * periods_per_year
        avg_dd = self.average_drawdown()
        return annual_return / abs(avg_dd) if avg_dd != 0 else float('inf')
    
    def burke_ratio(self, periods_per_year: int = 252) -> float:
        annual_return = self.returns.mean() * periods_per_year
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        drawdown_squared_sum = (drawdown[drawdown < 0] ** 2).sum()
        num_dd = len(drawdown[drawdown < 0])
        if num_dd == 0:
            return float('inf')
        burke_denom = np.sqrt(drawdown_squared_sum / num_dd)
        return annual_return / burke_denom if burke_denom != 0 else float('inf')
    
    def ulcer_index(self) -> float:
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        squared_dd = (drawdown ** 2).sum()
        return np.sqrt(squared_dd / len(drawdown))
    
    def pain_index(self) -> float:
        cumulative = (1 + self.returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return drawdown[drawdown < 0].abs().mean()
    
    def gain_to_pain_ratio(self) -> float:
        gains = self.returns[self.returns > 0].sum()
        pains = abs(self.returns[self.returns < 0].sum())
        return gains / pains if pains != 0 else float('inf')
    
    def tail_ratio(self) -> float:
        percentile_95 = self.returns.quantile(0.95)
        percentile_5 = self.returns.quantile(0.05)
        return abs(percentile_95 / percentile_5) if percentile_5 != 0 else float('inf')
    
    def omega_ratio(self, threshold: float = 0) -> float:
        gains = (self.returns - threshold).clip(lower=0).sum()
        losses = abs((self.returns - threshold).clip(upper=0).sum())
        return gains / losses if losses != 0 else float('inf')

class PortfolioRiskAggregator:
    def __init__(self, returns_df: pd.DataFrame):
        self.returns = returns_df
        self.assets = returns_df.columns.tolist()
        self.cov_matrix = returns_df.cov()
        self.corr_matrix = returns_df.corr()
    
    def portfolio_var(self, weights: np.ndarray, confidence: float = 0.95) -> float:
        portfolio_returns = (self.returns * weights).sum(axis=1)
        return AdvancedRiskMetrics(portfolio_returns).historical_var(confidence)
    
    def portfolio_cvar(self, weights: np.ndarray, confidence: float = 0.95) -> float:
        portfolio_returns = (self.returns * weights).sum(axis=1)
        return AdvancedRiskMetrics(portfolio_returns).historical_cvar(confidence)
    
    def marginal_var(self, weights: np.ndarray, asset_index: int, confidence: float = 0.95) -> float:
        portfolio_returns = (self.returns * weights).sum(axis=1)
        portfolio_var = AdvancedRiskMetrics(portfolio_returns).historical_var(confidence)
        asset_returns = self.returns.iloc[:, asset_index]
        
        if portfolio_var == 0:
            return 0
        
        covariance = asset_returns.cov(portfolio_returns)
        return covariance / portfolio_var
    
    def component_var(self, weights: np.ndarray, asset_index: int, confidence: float = 0.95) -> float:
        mvar = self.marginal_var(weights, asset_index, confidence)
        return mvar * weights[asset_index]
    
    def incremental_var(self, weights: np.ndarray, new_asset_returns: pd.Series, 
                        new_weight: float, confidence: float = 0.95) -> float:
        old_returns = (self.returns * weights).sum(axis=1)
        new_weights = np.append(weights, new_weight)
        new_returns = old_returns + new_asset_returns * new_weight
        old_var = AdvancedRiskMetrics(old_returns).historical_var(confidence)
        new_var = AdvancedRiskMetrics(new_returns).historical_var(confidence)
        return new_var - old_var
    
    def risk_decomposition(self, weights: np.ndarray, confidence: float = 0.95) -> pd.DataFrame:
        portfolio_returns = (self.returns * weights).sum(axis=1)
        portfolio_var = AdvancedRiskMetrics(portfolio_returns).historical_var(confidence)
        
        results = []
        for i, asset in enumerate(self.assets):
            mvar = self.marginal_var(weights, i, confidence)
            cvar = self.component_var(weights, i, confidence)
            pct_contribution = cvar / portfolio_var if portfolio_var != 0 else 0
            
            results.append({
                'asset': asset,
                'weight': weights[i],
                'marginal_var': mvar,
                'component_var': cvar,
                'risk_contribution_pct': pct_contribution
            })
        
        return pd.DataFrame(results)
