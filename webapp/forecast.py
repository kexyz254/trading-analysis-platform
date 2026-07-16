"""Multi-horizon direction forecasts for the pro dashboard.

For a (symbol, timeframe) pair this trains three small classifiers on
recent exchange history - one per horizon (next 1, 2, 3 closed candles) -
using the exact same engineered features the engine's own models train on
(trading_assistant.agents.features.build_features). Every probability is
returned WITH its own out-of-sample hit rate on a recent holdout window,
plus that window's always-up baseline, so the UI can always show how often
this model has actually been right lately instead of presenting a bare
percentage as if it were authority.

Dashboard-only analytics: nothing here feeds trading decisions, risk
checks, or the tournament. The engine's validated champion pipeline stays
the only path to real orders.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from trading_assistant.agents.features import FEATURE_COLUMNS, build_features
from trading_assistant.config import Config
from webapp import data_access as da

HORIZONS = (1, 2, 3)
HISTORY_BARS = 1000
HOLDOUT_BARS = 100
# Enough labeled rows to train on after the holdout is carved off - below
# this, refuse loudly rather than serve a model fit on almost nothing.
MIN_TRAIN_BARS = 150
# Retrain at most every 30 minutes per (symbol, timeframe). The prediction
# itself is recomputed from the latest closed candle on every request -
# only the model fit is this slow-moving.
_MODEL_TTL_SECONDS = 1800
# LRU cap on cached (symbol, timeframe) entries (3 models each) so browsing
# many timeframes can't grow the dashboard container's memory unboundedly.
_MAX_CACHED_ENTRIES = 8

_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _closed_bars_frame(config: Config, symbol: str, timeframe: str) -> pd.DataFrame:
    raw = da.fetch_ohlcv(config, symbol, timeframe, limit=HISTORY_BARS)
    frame = pd.DataFrame(raw)
    if frame.empty or len(frame) < 2:
        raise ValueError(f"exchange returned no usable history for {symbol} {timeframe}")
    # ccxt timestamps are ms epochs; build_features parses this column via
    # pd.to_datetime, which would read bare ints as NANOSECONDS and put
    # every bar in 1970 - convert explicitly.
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    # The final row is the still-forming candle: drop it so training labels
    # and the live prediction both stand on closed candles only.
    return frame.iloc[:-1].reset_index(drop=True)


def _probability_up(model: Any, X_latest: pd.DataFrame) -> float:
    proba = model.predict_proba(X_latest)[0]
    classes = list(model.classes_)
    if 1 not in classes:
        # Degenerate training window where price never closed higher -
        # the model has literally never seen an "up", report 0 honestly.
        return 0.0
    return float(proba[classes.index(1)])


def _fit_horizon(featured: pd.DataFrame, horizon: int) -> Dict[str, Any]:
    from sklearn.ensemble import RandomForestClassifier

    labeled = featured.copy()
    labeled["target"] = (labeled["close"].shift(-horizon) > labeled["close"]).astype(int)
    labeled = labeled.iloc[:-horizon]  # the last `horizon` rows have no future close yet

    holdout = min(HOLDOUT_BARS, len(labeled) // 5)
    if len(labeled) - holdout < MIN_TRAIN_BARS or holdout < 20:
        raise ValueError(
            f"not enough closed candles to train honestly: {len(labeled)} labeled rows "
            f"(need >= {MIN_TRAIN_BARS} to train plus >= 20 held out)"
        )

    X, y = labeled[FEATURE_COLUMNS], labeled["target"]

    def _new_model():
        return RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42, n_jobs=1)

    # Hit rate is measured strictly out-of-sample: fit on everything before
    # the holdout, score on the holdout. Then refit on all labeled rows so
    # the live prediction uses the freshest data available.
    eval_model = _new_model().fit(X.iloc[:-holdout], y.iloc[:-holdout])
    y_holdout = y.iloc[-holdout:]
    hit_rate = float((eval_model.predict(X.iloc[-holdout:]) == y_holdout).mean())
    up_share = float(y_holdout.mean())

    return {
        "model": _new_model().fit(X, y),
        "hit_rate": hit_rate,
        "holdout_bars": int(holdout),
        "up_share_holdout": up_share,
        "trained_rows": int(len(labeled) - holdout),
    }


def _get_or_train(featured: pd.DataFrame, symbol: str, timeframe: str) -> Dict[str, Any]:
    key = (symbol, timeframe)
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and now - entry["trained_at"] < _MODEL_TTL_SECONDS:
            return entry

    entry = {
        "trained_at": now,
        "horizons": {h: _fit_horizon(featured, h) for h in HORIZONS},
    }
    with _cache_lock:
        _cache[key] = entry
        while len(_cache) > _MAX_CACHED_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k]["trained_at"])
            del _cache[oldest]
    return entry


def get_forecast(config: Config, symbol: str, timeframe: str) -> Dict[str, Any]:
    frame = _closed_bars_frame(config, symbol, timeframe)
    featured = build_features(frame).dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)
    if featured.empty:
        raise ValueError(f"no rows with complete features for {symbol} {timeframe}")

    entry = _get_or_train(featured, symbol, timeframe)

    latest = featured[FEATURE_COLUMNS].iloc[[-1]]
    last_bar = featured.iloc[-1]

    horizons: List[Dict[str, Any]] = []
    for h in HORIZONS:
        fitted = entry["horizons"][h]
        p_up = _probability_up(fitted["model"], latest)
        horizons.append(
            {
                "step": h,
                "direction": "UP" if p_up >= 0.5 else "DOWN",
                "probability_up": p_up,
                "hit_rate": fitted["hit_rate"],
                "holdout_bars": fitted["holdout_bars"],
                "up_share_holdout": fitted["up_share_holdout"],
                "trained_rows": fitted["trained_rows"],
            }
        )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": last_bar["timestamp"].isoformat(),
        "last_close": float(last_bar["close"]),
        "model_trained_at": entry["trained_at"],
        "horizons": horizons,
    }
