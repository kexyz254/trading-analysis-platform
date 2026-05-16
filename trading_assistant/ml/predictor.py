import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

@dataclass
class Prediction:
    value: float
    confidence: float
    direction: int

class RandomForestPredictor:
    def __init__(self, n_trees=100, max_depth=10):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.model = None
        self.is_trained = False
    
    def train(self, X, y):
        from sklearn.ensemble import RandomForestRegressor
        self.model = RandomForestRegressor(
            n_estimators=self.n_trees,
            max_depth=self.max_depth,
            random_state=42,
            n_jobs=-1
        )
        self.model.fit(X, y)
        self.is_trained = True
    
    def predict(self, X):
        if not self.is_trained:
            return np.zeros(len(X))
        return self.model.predict(X)
    
    def get_importance(self, feature_names):
        if not self.is_trained:
            return {}
        return dict(zip(feature_names, self.model.feature_importances_))

class XGBPredictor:
    def __init__(self, n_estimators=100, lr=0.01, max_depth=5):
        self.n_estimators = n_estimators
        self.lr = lr
        self.max_depth = max_depth
        self.model = None
        self.is_trained = False
    
    def train(self, X, y):
        import xgboost as xgb
        self.model = xgb.XGBRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.lr,
            max_depth=self.max_depth,
            random_state=42
        )
        self.model.fit(X, y)
        self.is_trained = True
    
    def predict(self, X):
        if not self.is_trained:
            return np.zeros(len(X))
        return self.model.predict(X)

class FeatureEngineer:
    def __init__(self):
        self.feature_cols = []
    
    def create_lags(self, df, col, lags=[1,2,3,5,10,20]):
        for lag in lags:
            df[f'{col}_lag_{lag}'] = df[col].shift(lag)
        return df
    
    def create_rolling(self, df, col, windows=[5,10,20,50]):
        for w in windows:
            df[f'{col}_mean_{w}'] = df[col].rolling(w).mean()
            df[f'{col}_std_{w}'] = df[col].rolling(w).std()
        return df
    
    def transform(self, df, target_col=None):
        result = df.copy()
        
        for col in ['open', 'high', 'low', 'close']:
            if col in result.columns:
                result = self.create_lags(result, col)
                result = self.create_rolling(result, col)
        
        if 'volume' in result.columns:
            result = self.create_lags(result, 'volume')
            result = self.create_rolling(result, 'volume')
        
        result = result.dropna()
        
        exclude = ['timestamp'] if 'timestamp' in result.columns else []
        self.feature_cols = [c for c in result.columns if c not in exclude and c != target_col]
        
        return result

class EnsemblePredictor:
    def __init__(self, predictors, weights=None):
        self.predictors = predictors
        self.weights = weights or [1.0/len(predictors)] * len(predictors)
    
    def train(self, X, y):
        for p in self.predictors:
            p.train(X, y)
    
    def predict(self, X):
        predictions = []
        for p, w in zip(self.predictors, self.weights):
            pred = p.predict(X) * w
            predictions.append(pred)
        return np.sum(predictions, axis=0)
    
    def predict_with_confidence(self, X):
        predictions = []
        for p, w in zip(self.predictors, self.weights):
            pred = p.predict(X) * w
            predictions.append(pred)
        
        ensemble = np.sum(predictions, axis=0)
        uncertainty = np.std(predictions, axis=0)
        confidence = 1.0 / (1.0 + uncertainty)
        
        return ensemble, confidence
