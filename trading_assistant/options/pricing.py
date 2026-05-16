import numpy as np
from scipy import stats
from math import log, sqrt, exp
from dataclasses import dataclass
from typing import Tuple, Optional

@dataclass
class Option:
    S: float
    K: float
    T: float
    r: float
    sigma: float
    option_type: str

class BlackScholes:
    @staticmethod
    def d1(S, K, T, r, sigma):
        return (log(S/K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))
    
    @staticmethod
    def d2(S, K, T, r, sigma):
        return BlackScholes.d1(S, K, T, r, sigma) - sigma * sqrt(T)
    
    @staticmethod
    def call_price(S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        return S * stats.norm.cdf(d1) - K * exp(-r * T) * stats.norm.cdf(d2)
    
    @staticmethod
    def put_price(S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        return K * exp(-r * T) * stats.norm.cdf(-d2) - S * stats.norm.cdf(-d1)
    
    @staticmethod
    def delta(option_type, S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        if option_type == 'call':
            return stats.norm.cdf(d1)
        else:
            return stats.norm.cdf(d1) - 1
    
    @staticmethod
    def gamma(S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        return stats.norm.pdf(d1) / (S * sigma * sqrt(T))
    
    @staticmethod
    def vega(S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        return S * stats.norm.pdf(d1) * sqrt(T)
    
    @staticmethod
    def theta(option_type, S, K, T, r, sigma):
        d1 = BlackScholes.d1(S, K, T, r, sigma)
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        term1 = -S * stats.norm.pdf(d1) * sigma / (2 * sqrt(T))
        if option_type == 'call':
            term2 = r * K * exp(-r * T) * stats.norm.cdf(d2)
        else:
            term2 = -r * K * exp(-r * T) * stats.norm.cdf(-d2)
        return term1 + term2
    
    @staticmethod
    def rho(option_type, S, K, T, r, sigma):
        d2 = BlackScholes.d2(S, K, T, r, sigma)
        if option_type == 'call':
            return K * T * exp(-r * T) * stats.norm.cdf(d2)
        else:
            return -K * T * exp(-r * T) * stats.norm.cdf(-d2)

class BinomialTree:
    def __init__(self, steps=100):
        self.steps = steps
    
    def price(self, S, K, T, r, sigma, option_type):
        dt = T / self.steps
        u = exp(sigma * sqrt(dt))
        d = 1 / u
        p = (exp(r * dt) - d) / (u - d)
        disc = exp(-r * dt)
        
        stock_tree = np.zeros((self.steps + 1, self.steps + 1))
        option_tree = np.zeros((self.steps + 1, self.steps + 1))
        
        for i in range(self.steps + 1):
            stock_tree[i, self.steps] = S * (u ** i) * (d ** (self.steps - i))
        
        for i in range(self.steps + 1):
            if option_type == 'call':
                option_tree[i, self.steps] = max(0, stock_tree[i, self.steps] - K)
            else:
                option_tree[i, self.steps] = max(0, K - stock_tree[i, self.steps])
        
        for j in range(self.steps - 1, -1, -1):
            for i in range(j + 1):
                option_tree[i, j] = disc * (p * option_tree[i + 1, j + 1] + (1 - p) * option_tree[i, j + 1])
        
        return option_tree[0, 0]
    
    def american_price(self, S, K, T, r, sigma, option_type):
        dt = T / self.steps
        u = exp(sigma * sqrt(dt))
        d = 1 / u
        p = (exp(r * dt) - d) / (u - d)
        disc = exp(-r * dt)
        
        stock_tree = np.zeros((self.steps + 1, self.steps + 1))
        option_tree = np.zeros((self.steps + 1, self.steps + 1))
        
        for i in range(self.steps + 1):
            stock_tree[i, self.steps] = S * (u ** i) * (d ** (self.steps - i))
        
        for i in range(self.steps + 1):
            if option_type == 'call':
                option_tree[i, self.steps] = max(0, stock_tree[i, self.steps] - K)
            else:
                option_tree[i, self.steps] = max(0, K - stock_tree[i, self.steps])
        
        for j in range(self.steps - 1, -1, -1):
            for i in range(j + 1):
                hold = disc * (p * option_tree[i + 1, j + 1] + (1 - p) * option_tree[i, j + 1])
                if option_type == 'call':
                    exercise = max(0, stock_tree[i, j] - K)
                else:
                    exercise = max(0, K - stock_tree[i, j])
                option_tree[i, j] = max(hold, exercise)
        
        return option_tree[0, 0]

class Greeks:
    @staticmethod
    def calculate_all(S, K, T, r, sigma, option_type):
        return {
            'delta': BlackScholes.delta(option_type, S, K, T, r, sigma),
            'gamma': BlackScholes.gamma(S, K, T, r, sigma),
            'vega': BlackScholes.vega(S, K, T, r, sigma),
            'theta': BlackScholes.theta(option_type, S, K, T, r, sigma),
            'rho': BlackScholes.rho(option_type, S, K, T, r, sigma)
        }

class ImpliedVolatility:
    @staticmethod
    def implied_vol(S, K, T, r, market_price, option_type, tolerance=1e-6, max_iter=100):
        sigma_low = 0.01
        sigma_high = 5.0
        
        for i in range(max_iter):
            sigma_mid = (sigma_low + sigma_high) / 2
            if option_type == 'call':
                model_price = BlackScholes.call_price(S, K, T, r, sigma_mid)
            else:
                model_price = BlackScholes.put_price(S, K, T, r, sigma_mid)
            
            if abs(model_price - market_price) < tolerance:
                return sigma_mid
            
            if model_price > market_price:
                sigma_high = sigma_mid
            else:
                sigma_low = sigma_mid
        
        return (sigma_low + sigma_high) / 2

class OptionGreeks:
    @staticmethod
    def delta_gamma_hedge(delta1, gamma1, delta2, gamma2, target_delta, target_gamma):
        A = np.array([[delta1, delta2], [gamma1, gamma2]])
        b = np.array([target_delta, target_gamma])
        return np.linalg.solve(A, b)
    
    @staticmethod
    def vega_hedge(vega1, vega2, target_vega):
        if vega1 == vega2:
            return np.array([target_vega / (2 * vega1), target_vega / (2 * vega1)])
        
        A = np.array([[vega1, vega2]])
        b = np.array([target_vega])
        return np.linalg.lstsq(A, b, rcond=None)[0]

class VolatilitySurface:
    def __init__(self):
        self.implied_vols = {}
    
    def add_vol(self, strike, expiry, iv):
        self.implied_vols[(strike, expiry)] = iv
    
    def interpolate(self, strike, expiry, method='linear'):
        strikes = [k for k, e in self.implied_vols.keys() if e == expiry]
        vols = [self.implied_vols[(k, expiry)] for k in strikes]
        
        if method == 'linear':
            return np.interp(strike, strikes, vols)
        elif method == 'cubic':
            from scipy.interpolate import interp1d
            f = interp1d(strikes, vols, kind='cubic', fill_value='extrapolate')
            return float(f(strike))
        return vols[0]
    
    def smile_curve(self, S, expiry, moneyness_range=(0.8, 1.2), points=20):
        strikes = np.linspace(S * moneyness_range[0], S * moneyness_range[1], points)
        vols = [self.interpolate(k, expiry) for k in strikes]
        return strikes, vols

class MonteCarloOption:
    def __init__(self, n_simulations=10000, n_steps=252):
        self.n_simulations = n_simulations
        self.n_steps = n_steps
    
    def price_european(self, S, K, T, r, sigma, option_type):
        dt = T / self.n_steps
        np.random.seed(42)
        Z = np.random.standard_normal((self.n_simulations, self.n_steps))
        
        S_T = S * np.exp(np.cumsum((r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z, axis=1))[:, -1]
        
        if option_type == 'call':
        payoffs = np.maximum(S_T - K, 0)
        else:
            payoffs = np.maximum(K - S_T, 0)
        
        return exp(-r * T) * np.mean(payoffs)
    
    def price_asian(self, S, K, T, r, sigma, option_type):
        dt = T / self.n_steps
        np.random.seed(42)
        Z = np.random.standard_normal((self.n_simulations, self.n_steps))
        
        prices = S * np.exp(np.cumsum((r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z, axis=1))
        avg_prices = np.mean(prices, axis=1)
        
        if option_type == 'call':
            payoffs = np.maximum(avg_prices - K, 0)
        else:
            payoffs = np.maximum(K - avg_prices, 0)
        
        return exp(-r * T) * np.mean(payoffs)
    
    def price_barrier(self, S, K, T, r, sigma, barrier, barrier_type, option_type):
        dt = T / self.n_steps
        np.random.seed(42)
        Z = np.random.standard_normal((self.n_simulations, self.n_steps))
        
        paths = S * np.exp(np.cumsum((r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * Z, axis=1))
        
        if barrier_type == 'up_and_out':
            knocked_out = np.any(paths >= barrier, axis=1)
        elif barrier_type == 'down_and_out':
            knocked_out = np.any(paths <= barrier, axis=1)
        elif barrier_type == 'up_and_in':
            knocked_out = ~np.any(paths >= barrier, axis=1)
        else:
            knocked_out = ~np.any(paths <= barrier, axis=1)
        
        S_T = paths[:, -1]
        if option_type == 'call':
            payoffs = np.maximum(S_T - K, 0)
        else:
            payoffs = np.maximum(K - S_T, 0)
        
        payoffs[knocked_out] = 0
        
        return exp(-r * T) * np.mean(payoffs)
