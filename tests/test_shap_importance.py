import numpy as np
import pandas as pd
import pytest

from trading_assistant.ml.predictor import EnsemblePredictor, RandomForestPredictor, XGBPredictor
from trading_assistant.ml.shap_importance import compute_ensemble_feature_importance


def _synthetic_data(n=200, seed=0):
    """A target that depends STRONGLY on feature_a, weakly on feature_b,
    and not at all on feature_c/feature_d - gives SHAP something real to
    distinguish, rather than asserting on arbitrary noise."""
    rng = np.random.RandomState(seed)
    feature_a = rng.uniform(-1, 1, n)
    feature_b = rng.uniform(-1, 1, n)
    feature_c = rng.uniform(-1, 1, n)
    feature_d = rng.uniform(-1, 1, n)
    target = (feature_a > 0).astype(float) * 0.9 + (feature_b > 0.5).astype(float) * 0.1
    X = pd.DataFrame({"feature_a": feature_a, "feature_b": feature_b, "feature_c": feature_c, "feature_d": feature_d})
    return X, pd.Series(target)


class FakePredictorNoModel:
    """Mimics a predictor that was never trained (model attribute None) -
    e.g. one ensemble member that failed to train."""

    model = None


def test_returns_importance_for_every_feature_with_a_trained_ensemble():
    X, y = _synthetic_data()
    ensemble = EnsemblePredictor(
        predictors=[RandomForestPredictor(n_trees=20, max_depth=4), XGBPredictor(n_estimators=20, max_depth=3)]
    )
    ensemble.train(X, y)

    importance = compute_ensemble_feature_importance(ensemble, X, list(X.columns))

    assert importance is not None
    assert set(importance.keys()) == {"feature_a", "feature_b", "feature_c", "feature_d"}
    assert all(v >= 0 for v in importance.values())  # mean absolute SHAP is never negative


def test_the_dominant_feature_gets_the_highest_importance():
    """The actual point of using SHAP over a plain heuristic: it must
    correctly rank the feature the target actually depends on above ones
    it doesn't, not just return SOME numbers."""
    X, y = _synthetic_data()
    ensemble = EnsemblePredictor(
        predictors=[RandomForestPredictor(n_trees=30, max_depth=4), XGBPredictor(n_estimators=30, max_depth=3)]
    )
    ensemble.train(X, y)

    importance = compute_ensemble_feature_importance(ensemble, X, list(X.columns))

    assert importance["feature_a"] == max(importance.values())
    assert importance["feature_a"] > importance["feature_c"]
    assert importance["feature_a"] > importance["feature_d"]


def test_none_when_ensemble_has_no_predictors():
    class EmptyEnsemble:
        predictors = []
        weights = []

    X, _ = _synthetic_data(n=10)
    assert compute_ensemble_feature_importance(EmptyEnsemble(), X, list(X.columns)) is None


def test_none_when_every_predictor_is_untrained():
    class UntrainedEnsemble:
        predictors = [FakePredictorNoModel(), FakePredictorNoModel()]
        weights = [0.5, 0.5]

    X, _ = _synthetic_data(n=10)
    assert compute_ensemble_feature_importance(UntrainedEnsemble(), X, list(X.columns)) is None


def test_survives_a_predictor_whose_shap_computation_raises(monkeypatch):
    """A diagnostic feature must never be capable of breaking a real
    promotion - one ensemble member misbehaving must not prevent the
    other from still producing a usable result."""
    import trading_assistant.ml.shap_importance as module

    X, y = _synthetic_data()
    ensemble = EnsemblePredictor(
        predictors=[RandomForestPredictor(n_trees=20, max_depth=4), XGBPredictor(n_estimators=20, max_depth=3)]
    )
    ensemble.train(X, y)

    import shap

    real_tree_explainer = shap.TreeExplainer
    call_count = {"n": 0}

    def flaky_tree_explainer(model):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated SHAP failure")
        return real_tree_explainer(model)

    monkeypatch.setattr(shap, "TreeExplainer", flaky_tree_explainer)

    importance = compute_ensemble_feature_importance(ensemble, X, list(X.columns))

    assert importance is not None  # the second (working) predictor still produced a result


def test_none_when_shap_is_not_installed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "shap":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    X, y = _synthetic_data(n=10)
    ensemble = EnsemblePredictor(predictors=[RandomForestPredictor(n_trees=5, max_depth=2)])
    ensemble.train(X, y)

    assert compute_ensemble_feature_importance(ensemble, X, list(X.columns)) is None


def test_samples_rows_when_dataset_exceeds_max_sample_rows():
    """Large training sets must not make every retrain cycle proportionally
    slower - explained on a representative sample, not the full set."""
    X, y = _synthetic_data(n=2000)
    ensemble = EnsemblePredictor(predictors=[RandomForestPredictor(n_trees=10, max_depth=3)])
    ensemble.train(X, y)

    importance = compute_ensemble_feature_importance(ensemble, X, list(X.columns), sample_rows=100)

    assert importance is not None
    assert set(importance.keys()) == {"feature_a", "feature_b", "feature_c", "feature_d"}
