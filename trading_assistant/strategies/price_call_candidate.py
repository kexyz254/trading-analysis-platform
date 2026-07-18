"""
Price-Call ML candidate strategy.

A tournament candidate that turns the dashboard's heuristic price calls into a
statistically validated, self-adapting model. It mirrors ml_15m_candidate.py
exactly - build features, train two independent Platt-calibrated binary
classifiers, backtest a cost-aware strategy on walk-forward folds - and returns
the SAME metrics dict every other candidate returns, so the tournament promotes
it with identical rigour (min_folds / min_sharpe / min_pct_folds_profitable
gates, champion-switch hysteresis). No special treatment.

Two candidates are registered, one per real price-call horizon (5m, 15m). Both
operate on 15-minute bars (features from build_features_15m); the ONLY
difference is horizon_bars - how many bars ahead the label looks - which is
carried on the candidate's params, never hardcoded here.

Labels are a pure directional price call: "will the close be higher / lower
than the current close after `horizon_bars` bars?" - deliberately simpler than
ml_15m's ATR-threshold labels, because a price call predicts direction at a
fixed clock horizon, not a tradeable ATR-sized move.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from trading_assistant.agents.features_15m import build_features_15m
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection
from trading_assistant.market_data.feed_15m import FifteenMinuteFeed
# Reuse the exact calibration and cost-aware gate the 15m candidate uses, so
# the two share one battle-tested implementation rather than drifting.
from trading_assistant.strategies.ml_15m_candidate import (
    cost_aware_signal,
    train_calibrated_binary,
)

logger = logging.getLogger(__name__)


def make_price_call_targets(
    ohlcv: pd.DataFrame, horizon_bars: int = 1
) -> Tuple[pd.Series, pd.Series]:
    """Directional price-call labels.

    For each bar t, looking ahead `horizon_bars`:
      target_up   = 1 if close(t + horizon_bars) > close(t) else 0
      target_down = 1 if close(t + horizon_bars) < close(t) else 0

    The last `horizon_bars` rows have no future and are set NaN (dropped by the
    caller). A flat future (exactly equal close) is 0 for BOTH - it beat neither
    direction, which keeps the two labels honest rather than forcing a call.
    """
    close = ohlcv["close"]
    future_close = close.shift(-horizon_bars)
    target_up = (future_close > close).astype(float)
    target_down = (future_close < close).astype(float)
    if horizon_bars > 0:
        target_up.iloc[-horizon_bars:] = np.nan
        target_down.iloc[-horizon_bars:] = np.nan
    return target_up, target_down


def _empty_metrics() -> Dict[str, float]:
    return {
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate": 0.0,
        "num_trades": 0,
        "total_return_pct": 0.0,
        "profit_factor": 0.0,
    }


def _default_config() -> Dict[str, Any]:
    return {
        "horizon_bars": 1,
        "min_confidence": 0.55,
        "min_rr_ratio": 2.0,
        "fee_rate": 0.0015,
        "stop_loss_atr_mult": 2.0,
        "take_profit_atr_mult": 3.0,
    }


def _prepare(bars: pd.DataFrame, horizon_bars: int) -> Optional[pd.DataFrame]:
    """Features joined with directional targets, NaNs dropped. Returns None if
    there is nothing usable."""
    if bars is None or bars.empty:
        return None
    feat_df = build_features_15m(bars)
    if feat_df.empty:
        return None
    target_up, target_down = make_price_call_targets(bars, horizon_bars=horizon_bars)
    combined = feat_df.join(pd.DataFrame({"target_up": target_up, "target_down": target_down})).dropna()
    return combined if not combined.empty else None


def _train_pair(train_df: pd.DataFrame) -> Optional[Tuple[Any, Any, list]]:
    """Train + Platt-calibrate the up and down classifiers from a training
    frame (last 20% held out for calibration). Returns (model_up, model_down,
    feature_columns) or None if the split is too small or degenerate."""
    if len(train_df) < 50:
        return None
    calib_size = max(int(len(train_df) * 0.2), 10)
    train_train = train_df.iloc[:-calib_size]
    train_calib = train_df.iloc[-calib_size:]
    feature_cols = [c for c in train_df.columns if c not in ("target_up", "target_down")]

    X_train = train_train[feature_cols]
    X_calib = train_calib[feature_cols]
    y_tr_up, y_tr_dn = train_train["target_up"], train_train["target_down"]
    y_ca_up, y_ca_dn = train_calib["target_up"], train_calib["target_down"]

    # A calibrator needs both classes present in the calibration set; a
    # degenerate (all-up or all-down) window can't be calibrated honestly.
    if y_ca_up.nunique() < 2 or y_ca_dn.nunique() < 2 or y_tr_up.nunique() < 2 or y_tr_dn.nunique() < 2:
        return None
    try:
        model_up = train_calibrated_binary(X_train, y_tr_up, X_calib, y_ca_up)
        model_down = train_calibrated_binary(X_train, y_tr_dn, X_calib, y_ca_dn)
    except Exception:  # noqa: BLE001 - a bad fold must degrade to empty metrics, not crash the tournament
        logger.exception("price-call calibration failed")
        return None
    return model_up, model_down, feature_cols


def backtest_price_call(
    symbol: str,
    feed: FifteenMinuteFeed,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
    config: Dict[str, Any],
) -> Dict[str, float]:
    """One walk-forward fold: train calibrated up/down models on the training
    window, backtest a cost-aware directional strategy on the test window,
    return the standard tournament metrics dict."""
    cfg = _default_config()
    cfg.update(config or {})
    horizon_bars = int(cfg["horizon_bars"])

    warmup = pd.Timedelta(minutes=15 * 200)
    all_bars = feed.fetch_history(symbol, total_bars=2000)
    if all_bars is None or all_bars.empty:
        return _empty_metrics()
    bars = all_bars.loc[(train_start - warmup):test_end]
    if bars.empty:
        return _empty_metrics()

    combined = _prepare(bars, horizon_bars)
    feat_df = build_features_15m(bars)
    if combined is None or feat_df.empty:
        return _empty_metrics()

    train_df = combined[(combined.index >= train_start) & (combined.index < train_end)]
    test_df = combined[(combined.index >= test_start) & (combined.index < test_end)]
    if test_df.empty:
        return _empty_metrics()

    trained = _train_pair(train_df)
    if trained is None:
        return _empty_metrics()
    model_up, model_down, feature_cols = trained

    engine = BacktestEngineV2(initial_capital=10000.0)
    test_bars = bars.loc[test_start:test_end]
    position = None
    for idx, row in test_df.iterrows():
        if idx not in test_bars.index:
            continue
        price = float(test_bars.loc[idx, "close"])
        if price <= 0:
            continue
        feats = row[feature_cols].to_frame().T
        prob_up = float(model_up.predict_proba(feats)[0][1])
        prob_down = float(model_down.predict_proba(feats)[0][1])
        atr = float(feat_df.loc[idx, "atr"]) if ("atr" in feat_df.columns and idx in feat_df.index) else 0.0
        signal = cost_aware_signal(prob_up, prob_down, price, atr, cfg)
        if signal == 1 and position is None:
            qty = (engine.capital * 0.1) / price
            if engine.open_position(symbol, OrderDirection.LONG, price, idx, quantity=qty):
                position = engine.positions[-1]
        elif signal == -1 and position is not None:
            engine.close_position(position, price, idx)
            position = None
        engine.update_equity({symbol: price})

    if position is not None:
        last_price = float(test_bars.iloc[-1]["close"])
        engine.close_position(position, last_price, test_bars.index[-1])

    result = engine.get_result()
    closed = [p for p in engine.positions if p.exit_time is not None]
    gross_profit = sum(p.pnl for p in closed if p.pnl > 0)
    gross_loss = abs(sum(p.pnl for p in closed if p.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else (float("inf") if gross_profit > 0 else 0.0)
    return {
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "win_rate": result.win_rate,
        "num_trades": result.num_trades,
        "total_return_pct": result.total_return_pct,
        "profit_factor": profit_factor,
    }


def predict_live_probability(
    ohlcv_15m: pd.DataFrame, config: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Train the calibrated up/down models on all-but-the-last-`horizon_bars`
    closed bars and return the latest bar's calibrated probabilities - what the
    live price-call engine uses once this candidate is a promoted champion.

    Returns {probability_up, probability_down, trained_rows} or None when there
    is not enough history / a degenerate window.
    """
    cfg = _default_config()
    cfg.update(config or {})
    horizon_bars = int(cfg["horizon_bars"])

    combined = _prepare(ohlcv_15m, horizon_bars)
    if combined is None:
        return None
    trained = _train_pair(combined)
    if trained is None:
        return None
    model_up, model_down, feature_cols = trained

    feat_df = build_features_15m(ohlcv_15m)
    if feat_df.empty:
        return None
    latest = feat_df[feature_cols].iloc[[-1]]
    prob_up = float(model_up.predict_proba(latest)[0][1])
    prob_down = float(model_down.predict_proba(latest)[0][1])
    return {
        "probability_up": prob_up,
        "probability_down": prob_down,
        "trained_rows": int(len(combined)),
    }
