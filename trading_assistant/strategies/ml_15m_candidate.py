"""
15‑minute ML candidate strategy.

This module implements a complete trading candidate that operates on 15‑minute
bars. It builds features, creates binary targets (up move / down move) with an
ATR‑adaptive threshold, trains two independent calibrated binary classifiers,
and generates BUY/SELL/HOLD signals through a cost‑aware gate that respects
minimum confidence, expected profit, and risk‑reward ratio.

The candidate is designed to be evaluated by the tournament in the same way as
the existing 1h candidates – it receives a historical OHLCV dataframe, builds
features, and returns a backtest metrics dictionary. The only difference is that
it requires a 15‑minute data feed (FifteenMinuteFeed) and a configuration dict
with its specific parameters.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier

from trading_assistant.agents.features_15m import build_features_15m
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection
from trading_assistant.market_data.feed_15m import FifteenMinuteFeed

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Default model factory (two binary classifiers)
# ------------------------------------------------------------------
def default_15m_model_factory():
    """Return a new unfitted RandomForest for binary classification."""
    return RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)


# ------------------------------------------------------------------
# Target creation
# ------------------------------------------------------------------
def make_binary_targets(
    ohlcv_15m: pd.DataFrame,
    horizon_bars: int = 4,
    atr_multiplier: float = 0.5,
) -> Tuple[pd.Series, pd.Series]:
    """
    Create binary classification targets for upward and downward moves.

    For each bar t, look ahead `horizon_bars` bars (default 4 = 1 hour).
    - target_up   = 1 if future return >= atr_multiplier * ATR(t) else 0
    - target_down = 1 if future return <= -(atr_multiplier * ATR(t)) else 0

    Parameters
    ----------
    ohlcv_15m : pd.DataFrame
        Must contain 'close' and 'atr_pct' columns (from build_features_15m).
    horizon_bars : int
        Number of bars to look ahead.
    atr_multiplier : float
        Multiplier on current ATR to set the minimum move threshold.

    Returns
    -------
    (target_up, target_down) : tuple of pd.Series
        Labels aligned with the original index (NaN for the last horizon_bars rows).
    """
    close = ohlcv_15m["close"]
    atr_pct = ohlcv_15m["atr_pct"]
    future_close = close.shift(-horizon_bars)
    future_return = (future_close / close) - 1

    threshold = atr_multiplier * atr_pct

    target_up = (future_return >= threshold).astype(int)
    target_down = (future_return <= -threshold).astype(int)

    # The last `horizon_bars` rows have no valid future – set to NaN for later drop
    target_up.iloc[-horizon_bars:] = np.nan
    target_down.iloc[-horizon_bars:] = np.nan

    return target_up, target_down


# ------------------------------------------------------------------
# Model training with Platt calibration
# ------------------------------------------------------------------
def train_calibrated_binary(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_calib: pd.DataFrame,
    y_calib: pd.Series,
    model: Optional[Any] = None,
) -> Any:
    """
    Train a binary classifier and calibrate it with Platt scaling.

    Parameters
    ----------
    X_train, y_train : training set
    X_calib, y_calib : calibration set (held‑out from the training window)
    model : sklearn classifier (default: RandomForest)

    Returns
    -------
    calibrated_model : CalibratedClassifierCV
        A calibrated classifier ready to produce reliable probabilities.
    """
    if model is None:
        model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)

    # Train on training set
    model.fit(X_train, y_train)

    # Calibrate on the held-out calibration set (Platt scaling). The API for
    # calibrating an already-fitted estimator changed across sklearn versions:
    # <=1.5 used cv="prefit"; 1.6 introduced FrozenEstimator and 1.9 removed
    # "prefit" entirely. Prefer FrozenEstimator when present so the same code
    # calibrates cleanly on both old and new installs.
    try:
        from sklearn.frozen import FrozenEstimator  # sklearn >= 1.6

        calibrator = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
    except ImportError:  # pragma: no cover - exercised only on older sklearn
        calibrator = CalibratedClassifierCV(model, method="sigmoid", cv="prefit")
    calibrator.fit(X_calib, y_calib)
    return calibrator


# ------------------------------------------------------------------
# Cost‑aware trade signal
# ------------------------------------------------------------------
def cost_aware_signal(
    prob_up: float,
    prob_down: float,
    current_price: float,
    atr: float,
    config: Dict[str, Any],
) -> int:
    """
    Decide BUY (1), SELL (-1), or HOLD (0) based on calibrated probabilities
    and a cost‑aware risk‑reward check.

    Parameters
    ----------
    prob_up, prob_down : float
        Calibrated probabilities of a profitable up/down move.
    current_price : float
        Current bar's close price.
    atr : float
        Current 15‑minute ATR value (price units).
    config : dict
        Parameters:
        - min_confidence (float) : minimum probability to consider a signal
        - min_rr_ratio (float) : minimum reward/risk ratio
        - fee_rate (float) : estimated total fee + slippage as a decimal
        - stop_loss_atr_mult (float) : stop loss distance in ATR (e.g., 2.0)
        - take_profit_atr_mult (float) : take profit distance in ATR (e.g., 3.0)

    Returns
    -------
    int : 1 for BUY, -1 for SELL, 0 for HOLD
    """
    min_conf = config.get("min_confidence", 0.55)
    min_rr = config.get("min_rr_ratio", 2.0)
    fee = config.get("fee_rate", 0.0015)  # 0.15% combined
    stop_atr = config.get("stop_loss_atr_mult", 2.0)
    tp_atr = config.get("take_profit_atr_mult", 3.0)

    def expected_profit(signal_prob: float) -> float:
        # Expected profit = (TP * prob) - (SL * (1-prob)) - fee * price
        reward = tp_atr * atr * signal_prob
        risk = stop_atr * atr * (1 - signal_prob)
        cost = fee * current_price
        return reward - risk - cost

    # Buy signal
    if prob_up >= min_conf:
        exp_pnl = expected_profit(prob_up)
        rr_ratio = (tp_atr * atr) / (stop_atr * atr + fee * current_price)
        if exp_pnl > 0 and rr_ratio >= min_rr:
            return 1

    # Sell signal
    if prob_down >= min_conf:
        exp_pnl = expected_profit(prob_down)
        rr_ratio = (tp_atr * atr) / (stop_atr * atr + fee * current_price)
        if exp_pnl > 0 and rr_ratio >= min_rr:
            return -1

    return 0


# ------------------------------------------------------------------
# Walk‑forward backtest for the 15‑minute candidate
# ------------------------------------------------------------------
def backtest_ml_15m(
    symbol: str,
    feed: FifteenMinuteFeed,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
    config: Dict[str, Any],
    model_up: Optional[Any] = None,
    model_down: Optional[Any] = None,
) -> Dict[str, float]:
    """
    Train (or use provided) models on 15‑minute data, then backtest on the
    test period. Returns the standard metrics dict expected by the tournament.

    Parameters
    ----------
    symbol : str
    feed : FifteenMinuteFeed
    train_start, train_end : timestamps defining the training window
    test_start, test_end : timestamps defining the test window
    config : dict with keys:
        - horizon_bars (int)
        - atr_multiplier (float)
        - min_confidence (float)
        - min_rr_ratio (float)
        - fee_rate (float)
        - stop_loss_atr_mult (float)
        - take_profit_atr_mult (float)
    model_up, model_down : optional pre‑trained models (used for live paper trading)

    Returns
    -------
    dict : {sharpe_ratio, max_drawdown_pct, win_rate, num_trades, total_return_pct, profit_factor}
    """
    # Fetch full 15‑minute bars for the entire window plus warm‑up
    # (We fetch a bit extra before train_start for indicator warm‑up)
    warmup_bars = 200  # enough for rolling indicators
    full_start = train_start - pd.Timedelta(minutes=15 * warmup_bars)
    all_bars = feed.fetch_history(symbol, total_bars=2000)  # reasonable limit
    if all_bars.empty:
        return _empty_metrics()

    # Trim to our time window
    bars = all_bars.loc[full_start:test_end]
    if bars.empty:
        return _empty_metrics()

    # Build features
    feat_df = build_features_15m(bars)
    if feat_df.empty:
        return _empty_metrics()

    # Create targets. make_binary_targets needs the ATR threshold, which lives
    # on the engineered features (build_features_15m), not on the raw OHLCV the
    # feed returns - so join atr_pct on before labelling.
    bars_for_targets = bars.copy()
    bars_for_targets["atr_pct"] = feat_df["atr_pct"]
    target_up, target_down = make_binary_targets(
        bars_for_targets, horizon_bars=config.get("horizon_bars", 4),
        atr_multiplier=config.get("atr_multiplier", 0.5)
    )

    # Combine features with targets, drop NaN rows
    combined = feat_df.join(pd.DataFrame({"target_up": target_up, "target_down": target_down})).dropna()
    if combined.empty:
        return _empty_metrics()

    # Split into train and test based on time
    train_mask = (combined.index >= train_start) & (combined.index < train_end)
    test_mask = (combined.index >= test_start) & (combined.index < test_end)

    train_df = combined[train_mask]
    test_df = combined[test_mask]

    if len(train_df) < 50 or len(test_df) == 0:
        return _empty_metrics()

    # Further split train into train_train and train_calib (last 20% for calibration)
    calib_size = max(int(len(train_df) * 0.2), 10)
    train_train = train_df.iloc[:-calib_size]
    train_calib = train_df.iloc[-calib_size:]

    X_train = train_train.drop(columns=["target_up", "target_down"])
    y_train_up = train_train["target_up"]
    y_train_down = train_train["target_down"]
    X_calib = train_calib.drop(columns=["target_up", "target_down"])
    y_calib_up = train_calib["target_up"]
    y_calib_down = train_calib["target_down"]

    X_test = test_df.drop(columns=["target_up", "target_down"])

    # Train and calibrate models if not provided
    if model_up is None:
        model_up = train_calibrated_binary(X_train, y_train_up, X_calib, y_calib_up)
    if model_down is None:
        model_down = train_calibrated_binary(X_train, y_train_down, X_calib, y_calib_down)

    # Run backtest on test period
    engine = BacktestEngineV2(initial_capital=10000.0)
    test_bars = bars.loc[test_start:test_end]  # raw prices for the test window

    position = None
    for idx, row in X_test.iterrows():
        if idx not in test_bars.index:
            continue
        price = float(test_bars.loc[idx, "close"])
        if price <= 0:
            continue

        # Get probabilities
        prob_up = model_up.predict_proba([row])[0][1]  # probability of class 1
        prob_down = model_down.predict_proba([row])[0][1]

        # Get current ATR from features
        atr = feat_df.loc[idx, "atr"] if idx in feat_df.index else 0.0

        signal = cost_aware_signal(prob_up, prob_down, price, atr, config)

        # Open/close positions (long only for now; short can be added)
        if signal == 1 and position is None:
            quantity = (engine.capital * 0.1) / price  # simple risk sizing, can be improved
            if engine.open_position(symbol, OrderDirection.LONG, price, idx, quantity=quantity):
                position = engine.positions[-1]
        elif signal == -1 and position is not None:
            engine.close_position(position, price, idx)
            position = None

        engine.update_equity({symbol: price})

    if position is not None:
        last_price = float(test_bars.iloc[-1]["close"])
        engine.close_position(position, last_price, test_bars.index[-1])

    result = engine.get_result()
    # Compute profit factor from engine trades
    closed = [p for p in engine.positions if p.exit_time is not None]
    gross_profit = sum(p.pnl for p in closed if p.pnl > 0)
    gross_loss = abs(sum(p.pnl for p in closed if p.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

    return {
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "win_rate": result.win_rate,
        "num_trades": result.num_trades,
        "total_return_pct": result.total_return_pct,
        "profit_factor": profit_factor,
    }


def _empty_metrics() -> Dict[str, float]:
    return {
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate": 0.0,
        "num_trades": 0,
        "total_return_pct": 0.0,
        "profit_factor": 0.0,
    }