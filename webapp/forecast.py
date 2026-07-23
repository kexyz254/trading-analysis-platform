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

import copy
import hashlib
import pickle
import threading
import time
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from trading_assistant.agents.features import FEATURE_COLUMNS, build_features
from trading_assistant.analysis.chart_patterns import PATTERN_COLUMNS, pattern_feature_frame
from trading_assistant.config import Config
from webapp import data_access as da

# The dashboard models train on the engine's engineered features PLUS the
# chart-pattern feature layer (trading_assistant.analysis.chart_patterns).
# The per-horizon holdout hit rate therefore directly measures whether the
# classical patterns add any real predictive information on this symbol/
# timeframe - which is the honest way to "evaluate" them. The LIVE engine's
# models deliberately do NOT get these columns here: changing what real
# money trades on requires a validated retrain cycle, not a dashboard edit.
MODEL_FEATURES = FEATURE_COLUMNS + PATTERN_COLUMNS

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

# Featured-frame cache: build_features + pattern_feature_frame over ~1000
# bars is the expensive part of a request (a per-bar Python loop with
# repeated line fits), and its output only changes when a candle CLOSES -
# yet the frontend polls every 10s and the price-call loop every 60s. Keyed
# by the last closed bar's timestamp so a new candle invalidates it
# immediately; same LRU bound as the model cache.
_frame_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _featured_frame(frame: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    key = (symbol, timeframe)
    last_ts = frame.iloc[-1]["timestamp"]
    with _cache_lock:
        entry = _frame_cache.get(key)
        if entry is not None and entry["last_ts"] == last_ts and entry["n_bars"] == len(frame):
            return entry["featured"]

    featured = build_features(frame)
    featured[PATTERN_COLUMNS] = pattern_feature_frame(frame).values
    featured = featured.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)

    with _cache_lock:
        _frame_cache[key] = {"last_ts": last_ts, "n_bars": len(frame), "featured": featured}
        while len(_frame_cache) > _MAX_CACHED_ENTRIES:
            del _frame_cache[next(iter(_frame_cache))]
    return featured


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

    X, y = labeled[MODEL_FEATURES], labeled["target"]

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
        "total_labeled_rows": int(len(labeled)),
        "label_up_rate": float(y.mean()),
        "training_window_start": labeled.iloc[0]["timestamp"].isoformat(),
        "training_window_end": featured.iloc[-1]["timestamp"].isoformat(),
    }


def _model_fingerprint(horizons: Dict[int, Dict[str, Any]]) -> str:
    """Return an opaque identity for the exact fitted model instances."""
    payload = [
        (horizon, horizons[horizon]["model"])
        for horizon in sorted(horizons)
    ]
    return hashlib.sha256(pickle.dumps(payload, protocol=5)).hexdigest()


def _get_or_train(featured: pd.DataFrame, symbol: str, timeframe: str) -> Dict[str, Any]:
    key = (symbol, timeframe)
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and now - entry["trained_at"] < _MODEL_TTL_SECONDS:
            return entry

    horizons = {h: _fit_horizon(featured, h) for h in HORIZONS}
    entry = {
        "trained_at": now,
        "trained_at_ms": int(now * 1000),
        "model_fingerprint": _model_fingerprint(horizons),
        "horizons": horizons,
        "last_inference_at_ms": None,
        "last_as_of": None,
        "inference_count": 0,
    }
    with _cache_lock:
        _cache[key] = entry
        while len(_cache) > _MAX_CACHED_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k]["trained_at"])
            del _cache[oldest]
    return entry


def get_forecast(config: Config, symbol: str, timeframe: str) -> Dict[str, Any]:
    frame = _closed_bars_frame(config, symbol, timeframe)
    featured = _featured_frame(frame, symbol, timeframe)
    if featured.empty:
        raise ValueError(f"no rows with complete features for {symbol} {timeframe}")

    entry = _get_or_train(featured, symbol, timeframe)

    latest = featured[MODEL_FEATURES].iloc[[-1]]
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
                "total_labeled_rows": fitted["total_labeled_rows"],
                "label_up_rate": fitted["label_up_rate"],
                "training_window_start": fitted["training_window_start"],
                "training_window_end": fitted["training_window_end"],
            }
        )

    inference_at_ms = int(time.time() * 1000)
    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": last_bar["timestamp"].isoformat(),
        "last_close": float(last_bar["close"]),
        "model_trained_at": entry["trained_at"],
        "model_fingerprint": entry["model_fingerprint"],
        "last_inference_at_ms": inference_at_ms,
        "evidence_scope": "DASHBOARD_ANALYTICS_ONLY",
        "closed_candles_only": True,
        "execution_authority": False,
        "promotion_authority": False,
        "horizons": horizons,
    }
    with _cache_lock:
        entry["last_inference_at_ms"] = inference_at_ms
        entry["last_as_of"] = last_bar["timestamp"].isoformat()
        entry["inference_count"] = int(entry.get("inference_count", 0)) + 1
        # The interpreter reads only this completed, immutable-style snapshot.
        # It never triggers model fitting or reaches into estimator objects.
        entry["latest_forecast"] = copy.deepcopy(result)

    return result


def get_latest_forecast_snapshot(symbol: str, timeframe: str) -> Dict[str, Any] | None:
    """Return the last completed dashboard forecast without training.

    A missing cache entry is honest ``None`` evidence.  The Overall Market
    Interpreter must remain downstream of Forecast ML and may never make a
    read request mutate model state.
    """
    key = (str(symbol).upper(), str(timeframe))
    with _cache_lock:
        entry = _cache.get(key)
        snapshot = entry.get("latest_forecast") if entry else None
        return copy.deepcopy(snapshot) if snapshot is not None else None


def get_forecast_health(symbol: str, timeframe: str) -> Dict[str, Any]:
    """Read cached training/inference telemetry without triggering a model fit."""
    key = (str(symbol).upper(), str(timeframe))
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return {
                "state": "NO_EVIDENCE",
                "model_fingerprint": None,
                "trained_at_ms": None,
                "last_inference_at_ms": None,
                "last_processed_candle": None,
                "inference_count": 0,
                "horizons": [],
            }
        return {
            "state": "READY",
            "model_fingerprint": entry["model_fingerprint"],
            "trained_at_ms": entry["trained_at_ms"],
            "last_inference_at_ms": entry["last_inference_at_ms"],
            "last_processed_candle": entry["last_as_of"],
            "inference_count": int(entry["inference_count"]),
            "horizons": [
                {
                    "step": horizon,
                    "trained_rows": entry["horizons"][horizon]["trained_rows"],
                    "holdout_bars": entry["horizons"][horizon]["holdout_bars"],
                    "label_up_rate": entry["horizons"][horizon]["label_up_rate"],
                    "training_window_start": entry["horizons"][horizon]["training_window_start"],
                    "training_window_end": entry["horizons"][horizon]["training_window_end"],
                }
                for horizon in sorted(entry["horizons"])
            ],
        }
