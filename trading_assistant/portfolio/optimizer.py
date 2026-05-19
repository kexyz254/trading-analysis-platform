# trading_assistant/portfolio/optimizer.py

"""
Portfolio Optimization Module
"""

import numpy as np
import pandas as pd

class PortfolioOptimizer:
    def __init__(self, returns: pd.DataFrame):
        self.returns = returns

    def optimize_equal_weights(self):
        n_assets = self.returns.shape[1]
        weights = np.ones(n_assets) / n_assets
        return weights

    def optimize_min_variance(self):
        cov_matrix = self.returns.cov()
        n_assets = cov_matrix.shape[0]
        inv_cov = np.linalg.pinv(cov_matrix)
        ones = np.ones(n_assets)
        weights = inv_cov.dot(ones) / ones.dot(inv_cov).dot(ones)
        return weights