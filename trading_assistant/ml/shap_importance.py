"""SHAP-based feature importance for the promoted ensemble models.

Unlike sklearn's built-in feature_importances_ (Gini/gain-based - biased
toward high-cardinality continuous features, and gives no sense of
DIRECTION), SHAP values are additive and consistent: each one tells you
how much and which way a specific feature pushed a specific prediction,
and their mean absolute value across many rows gives a global importance
ranking that's directly comparable across features. Computed via
TreeExplainer, which is exact (no sampling approximation of the
explanation itself) and fast specifically for tree-ensemble models - both
RandomForest and XGBoost, the only two model types this system trains.

This is a diagnostic/QA tool, not a promotion gate: it tells you which of
the ~30 engineered features a promoted model actually relies on, so
noise features can be identified and pruned over time. It must never be
capable of blocking or altering a real promotion decision - every
failure mode here is caught and logged, never raised.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# SHAP's TreeExplainer is exact but its cost scales with the number of
# rows explained - explaining a full training set (thousands of rows)
# would make every retrain cycle meaningfully slower for no added
# accuracy over a representative sample.
MAX_SAMPLE_ROWS = 500


def compute_ensemble_feature_importance(
    ensemble: Any, X: pd.DataFrame, feature_names: List[str], sample_rows: int = MAX_SAMPLE_ROWS
) -> Optional[Dict[str, float]]:
    """Mean absolute SHAP value per feature, combined across the
    ensemble's underlying predictors using the SAME weights the ensemble
    itself predicts with - consistent with how its actual output is a
    weighted sum, not an unweighted average across models that may
    disagree with each other.

    Returns None (never raises) if shap isn't installed, the ensemble
    has no usable predictors, or every member's importance computation
    fails - a diagnostic feature must never be capable of breaking a
    real retrain/promotion cycle.
    """
    try:
        import shap
    except ImportError:
        logger.warning("shap is not installed - skipping feature importance computation")
        return None

    predictors = getattr(ensemble, "predictors", None)
    if not predictors:
        return None
    weights = getattr(ensemble, "weights", None) or [1.0 / len(predictors)] * len(predictors)

    X_sample = X.sample(n=sample_rows, random_state=42) if len(X) > sample_rows else X

    combined = np.zeros(len(feature_names))
    total_weight = 0.0
    for predictor, weight in zip(predictors, weights):
        model = getattr(predictor, "model", None)
        if model is None:
            continue
        try:
            explainer = shap.TreeExplainer(model)
            shap_values = np.asarray(explainer.shap_values(X_sample))
        except Exception:  # noqa: BLE001 - a diagnostic feature must never break a real retrain
            logger.exception("could not compute SHAP values for one ensemble member")
            continue
        # Both underlying models here are trained as regressors (see
        # ml.predictor's *Regressor classes) predicting a continuous
        # 0-1 next-bar-direction target, not a classifier - shap_values
        # is a single 2D (rows, features) array, never the list-of-
        # per-class-arrays some classifiers produce.
        mean_abs = np.abs(shap_values).mean(axis=0)
        combined += mean_abs * weight
        total_weight += weight

    if total_weight == 0.0:
        return None
    combined /= total_weight
    return dict(zip(feature_names, combined.tolist()))
