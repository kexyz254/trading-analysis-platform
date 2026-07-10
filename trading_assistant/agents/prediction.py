"""PredictionAgent: turns a market snapshot into a Signal + confidence, with per-symbol model persistence.

Each symbol gets its own model. A model trained on BTC/USDT's price-action
patterns has no validated bearing on ETH/USDT's - sharing one model across
symbols would silently apply an unvalidated model to every symbol except
the one it was actually backtested against.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
import numpy as np
import pandas as pd

from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import AIStrategy

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path("trading_assistant") / "models"


class PredictProbaAdapter:
    """Wraps an EnsemblePredictor (predict/predict_with_confidence, no predict_proba)
    so it satisfies the sklearn-style predict_proba interface AIStrategy expects.

    Without this, AIStrategy can't use an EnsemblePredictor's probability
    branch at all and silently falls back to sign-of-value predictions,
    throwing away the ensemble's confidence score.
    """

    def __init__(self, ensemble: Any):
        self.ensemble = ensemble
        self.last_confidence: Optional[float] = None

    def predict_proba(self, X) -> np.ndarray:
        # Reset before attempting: if predict_with_confidence raises, a stale
        # value from a previous successful call must not linger and get
        # journaled alongside this cycle's HOLD as if it were real.
        self.last_confidence = None
        predictions, confidence = self.ensemble.predict_with_confidence(X)
        self.last_confidence = float(np.mean(confidence))
        probability_up = np.clip(predictions, 0.0, 1.0)
        return np.column_stack([1 - probability_up, probability_up])


@dataclass
class PredictionResult:
    symbol: str
    signal: Signal
    confidence: Optional[float]
    # The model's raw probability-of-up that buy_threshold/sell_threshold
    # were actually applied to - distinct from `confidence` (an ensemble
    # agreement metric). This is what answers "how close to trading was
    # this HOLD", which `confidence` alone cannot: a model can be highly
    # confident/consistent while still predicting a neutral ~0.5.
    probability_up: Optional[float] = None


def _symbol_slug(symbol: str) -> str:
    return symbol.replace("/", "_")


class PredictionAgent:
    """Owns one AIStrategy (model + feature_columns) per symbol, with joblib-based
    per-symbol persistence. No model persistence existed anywhere in the codebase
    before this - a trained model could not be saved or reloaded across restarts."""

    def __init__(
        self,
        buy_threshold: float = 0.6,
        sell_threshold: float = 0.4,
        model_dir: Path = DEFAULT_MODEL_DIR,
        auto_load: bool = True,
    ):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.model_dir = Path(model_dir)
        self.auto_load = auto_load
        self._strategies: Dict[str, AIStrategy] = {}

    @staticmethod
    def _wrap_model(model: Any) -> Any:
        if model is None:
            return None
        if hasattr(model, "predict_proba"):
            return model
        if hasattr(model, "predict_with_confidence"):
            return PredictProbaAdapter(model)
        return model

    def _model_path(self, symbol: str, path: Optional[Path] = None) -> Path:
        return Path(path) if path is not None else self.model_dir / f"{_symbol_slug(symbol)}.joblib"

    def model_version(self, symbol: str, path: Optional[Path] = None) -> Optional[str]:
        """A real, verifiable version identifier for the audit trail - the
        ISO timestamp the currently-persisted model file for this symbol
        was last written (via save_model/promote_model), not a fabricated
        counter. Returns None when no model file exists on disk yet (e.g.
        a fresh symbol that hasn't been promoted, or a model injected
        in-memory only via set_model) - honest about there being nothing
        to version, rather than inventing a placeholder."""
        model_path = self._model_path(symbol, path)
        if not model_path.exists():
            return None
        mtime = model_path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    def _get_or_create_strategy(self, symbol: str) -> AIStrategy:
        if symbol not in self._strategies:
            model, feature_columns = self._try_auto_load(symbol) if self.auto_load else (None, None)
            self._strategies[symbol] = AIStrategy(
                model=self._wrap_model(model),
                feature_columns=feature_columns,
                buy_threshold=self.buy_threshold,
                sell_threshold=self.sell_threshold,
                name=f"AI Strategy ({symbol})",
            )
        return self._strategies[symbol]

    @staticmethod
    def _unpack_bundle(loaded: Any) -> tuple:
        # New format (see save_model): {"model": ..., "feature_columns": ...}.
        # Old format (pre-existing .joblib files saved before this bundle
        # existed): the bare model object, with no feature_columns - fall
        # back to None rather than erroring, since AIStrategy.generate_signal
        # already handles empty feature_columns safely (uses the full frame).
        if isinstance(loaded, dict) and "model" in loaded:
            return loaded["model"], loaded.get("feature_columns")
        return loaded, None

    def _try_auto_load(self, symbol: str) -> tuple:
        path = self._model_path(symbol)
        if not path.exists():
            return None, None
        try:
            return self._unpack_bundle(joblib.load(path))
        except Exception:  # noqa: BLE001 - a corrupt/incompatible model file shouldn't crash startup
            logger.exception("failed to auto-load saved model", extra={"context": {"symbol": symbol, "path": str(path)}})
            return None, None

    def set_feature_columns(self, symbol: str, feature_columns: list) -> None:
        self._get_or_create_strategy(symbol).feature_columns = feature_columns

    def set_model(self, symbol: str, model: Any, feature_columns: Optional[list] = None) -> None:
        """Directly inject a model for a symbol without touching disk - mainly for tests."""
        strategy = self._get_or_create_strategy(symbol)
        strategy.model = self._wrap_model(model)
        if feature_columns is not None:
            strategy.feature_columns = feature_columns

    def predict(self, symbol: str, features: pd.DataFrame) -> PredictionResult:
        strategy = self._get_or_create_strategy(symbol)
        signal = strategy.generate_signal(features)
        confidence = None
        if isinstance(strategy.model, PredictProbaAdapter):
            confidence = strategy.model.last_confidence
        return PredictionResult(
            symbol=symbol, signal=signal, confidence=confidence, probability_up=strategy.last_probability_up
        )

    def load_model(self, symbol: str, path: Optional[Path] = None) -> None:
        path = self._model_path(symbol, path)
        model, feature_columns = self._unpack_bundle(joblib.load(path))
        strategy = self._get_or_create_strategy(symbol)
        strategy.model = self._wrap_model(model)
        if feature_columns is not None:
            strategy.feature_columns = feature_columns
        logger.info("model loaded", extra={"context": {"symbol": symbol, "path": str(path)}})

    def save_model(
        self, symbol: str, model: Any, feature_columns: Optional[list] = None, path: Optional[Path] = None
    ) -> Path:
        path = self._model_path(symbol, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bundled with feature_columns (not just the bare model): a model
        # reloaded in a fresh process (auto_load, or an explicit load_model)
        # otherwise has no way to know which columns it was trained on, and
        # AIStrategy would silently pass it the full, wrong feature set -
        # sklearn raises immediately, but only once something actually calls
        # predict() on that mismatched model.
        joblib.dump({"model": model, "feature_columns": feature_columns}, path)
        logger.info("model saved", extra={"context": {"symbol": symbol, "path": str(path)}})
        return path

    def _previous_model_path(self, symbol: str, path: Optional[Path] = None) -> Path:
        current = self._model_path(symbol, path)
        return current.with_suffix(".previous.joblib")

    def promote_model(
        self, symbol: str, model: Any, feature_columns: list, path: Optional[Path] = None
    ) -> Path:
        """Persist and atomically swap in a newly trained model for a symbol.

        Builds a full replacement AIStrategy first, then swaps it into
        `_strategies` with a single dict assignment - safe even if a live
        `predict()` call is in flight on another thread (e.g. a background
        retrain running via asyncio.to_thread alongside the live loop):
        a concurrent reader always sees either the fully-old or
        fully-new strategy, never a torn mix of new model + stale
        feature_columns (or vice versa).

        Keeps exactly one prior version on disk (see rollback_model) -
        without this, a live-performance-triggered rollback would only be
        able to revert in-memory state, and lose that ability the moment
        the process restarts.
        """
        current_path = self._model_path(symbol, path)
        if current_path.exists():
            shutil.copy2(current_path, self._previous_model_path(symbol, path))
        saved_path = self.save_model(symbol, model, feature_columns, path)
        new_strategy = AIStrategy(
            model=self._wrap_model(model),
            feature_columns=feature_columns,
            buy_threshold=self.buy_threshold,
            sell_threshold=self.sell_threshold,
            name=f"AI Strategy ({symbol})",
        )
        self._strategies[symbol] = new_strategy
        logger.info("model promoted", extra={"context": {"symbol": symbol, "path": str(saved_path)}})
        return saved_path

    def rollback_model(self, symbol: str, path: Optional[Path] = None) -> bool:
        """Revert to the model that was live immediately before the most
        recent promotion - the "learning changes can be rolled back"
        capability, triggered by LearningAgent.check_live_decay when a
        currently-deployed model's REAL performance falls well short of
        what it validated at during walk-forward. Returns False (a no-op,
        not an error) if there's no previous version to revert to - e.g.
        the very first promotion for this symbol ever made."""
        previous_path = self._previous_model_path(symbol, path)
        if not previous_path.exists():
            return False
        model, feature_columns = self._unpack_bundle(joblib.load(previous_path))
        strategy = self._get_or_create_strategy(symbol)
        strategy.model = self._wrap_model(model)
        if feature_columns is not None:
            strategy.feature_columns = feature_columns
        # Also persist the rollback as the new "current" model, so a
        # future restart's auto_load picks up the reverted model, not the
        # decayed one it just replaced.
        self.save_model(symbol, model, feature_columns, path)
        logger.warning("model rolled back to previous version", extra={"context": {"symbol": symbol}})
        return True
