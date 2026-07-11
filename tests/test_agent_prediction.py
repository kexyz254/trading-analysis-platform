import numpy as np
import pandas as pd
import pytest

from trading_assistant.agents.prediction import PredictionAgent, PredictProbaAdapter
from trading_assistant.strategies.base import Signal


class DummyClassifier:
    def __init__(self, up_probability=0.8):
        self.up_probability = up_probability

    def predict_proba(self, X):
        return np.array([[1 - self.up_probability, self.up_probability]])


class DummyEnsemble:
    """Mimics ml.predictor.EnsemblePredictor: predict_with_confidence, no predict_proba."""

    def __init__(self, prediction=0.9, confidence=0.7):
        self.prediction = prediction
        self.confidence = confidence

    def predict_with_confidence(self, X):
        n = len(X)
        return np.full(n, self.prediction), np.full(n, self.confidence)


def _features():
    return pd.DataFrame({"close": [100.0, 101.0, 102.0]})


def test_predict_with_probability_classifier_emits_buy():
    agent = PredictionAgent(auto_load=False)
    agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.8), feature_columns=["close"])

    result = agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.BUY
    assert result.symbol == "BTC/USDT"
    assert result.probability_up == 0.8


def test_predict_with_no_model_holds():
    agent = PredictionAgent(auto_load=False)

    result = agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.HOLD
    assert result.confidence is None


def test_predict_proba_adapter_wraps_ensemble_and_reports_confidence():
    agent = PredictionAgent(auto_load=False)
    ensemble = DummyEnsemble(prediction=0.9, confidence=0.65)
    agent.set_model("BTC/USDT", ensemble, feature_columns=["close"])

    assert isinstance(agent._get_or_create_strategy("BTC/USDT").model, PredictProbaAdapter)

    result = agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.BUY
    assert result.confidence == 0.65


def test_predict_exposes_probability_up_distinct_from_confidence():
    """Regression: confidence (ensemble agreement) and probability_up (what
    buy/sell thresholds are actually applied to) are different numbers - a
    model can report high confidence while predicting a neutral ~0.5, and
    without this field a HOLD looks identical to "no real signal" even when
    it was a near-miss."""
    agent = PredictionAgent(auto_load=False, buy_threshold=0.6, sell_threshold=0.4)
    ensemble = DummyEnsemble(prediction=0.51, confidence=0.98)
    agent.set_model("BTC/USDT", ensemble, feature_columns=["close"])

    result = agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.HOLD
    assert result.confidence == 0.98
    assert result.probability_up == pytest.approx(0.51)


def test_models_are_isolated_per_symbol():
    """A model set for one symbol must never answer predictions for another symbol."""
    agent = PredictionAgent(auto_load=False)
    agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])
    agent.set_model("ETH/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])

    btc_result = agent.predict("BTC/USDT", _features())
    eth_result = agent.predict("ETH/USDT", _features())

    assert btc_result.signal == Signal.BUY
    assert eth_result.signal == Signal.SELL


def test_save_and_load_model_round_trip(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    saved_path = agent.save_model("BTC/USDT", DummyClassifier(up_probability=0.9))

    assert saved_path.exists()

    reloaded_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    reloaded_agent.load_model("BTC/USDT")
    reloaded_agent.set_feature_columns("BTC/USDT", ["close"])

    result = reloaded_agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.BUY


def test_model_version_is_none_when_no_model_file_exists(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    assert agent.model_version("BTC/USDT") is None


def test_model_version_reflects_the_saved_model_files_mtime(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    saved_path = agent.save_model("BTC/USDT", DummyClassifier(up_probability=0.9))

    version = agent.model_version("BTC/USDT")

    assert version is not None
    import os
    from datetime import datetime, timezone

    expected = datetime.fromtimestamp(os.path.getmtime(saved_path), tz=timezone.utc).isoformat()
    assert version == expected


def test_model_version_changes_after_a_re_promotion(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.save_model("BTC/USDT", DummyClassifier(up_probability=0.9))
    first_version = agent.model_version("BTC/USDT")

    import time

    time.sleep(0.01)
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
    second_version = agent.model_version("BTC/USDT")

    assert second_version != first_version


def test_auto_load_picks_up_previously_saved_model_for_that_symbol(tmp_path):
    bootstrap = PredictionAgent(model_dir=tmp_path, auto_load=False)
    bootstrap.save_model("BTC/USDT", DummyClassifier(up_probability=0.1))

    agent = PredictionAgent(model_dir=tmp_path, auto_load=True)
    agent.set_feature_columns("BTC/USDT", ["close"])

    result = agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.SELL

    # ETH/USDT has no saved model, so it must still HOLD, not fall back to BTC's model.
    other_result = agent.predict("ETH/USDT", _features())
    assert other_result.signal == Signal.HOLD


class FlakyEnsemble:
    """First call succeeds, second call raises - simulates a live prediction
    failure on a later cycle (e.g. from a NaN feature)."""

    def __init__(self, prediction=0.9, confidence=0.8):
        self.prediction = prediction
        self.confidence = confidence
        self.call_count = 0

    def predict_with_confidence(self, X):
        self.call_count += 1
        if self.call_count > 1:
            raise ValueError("simulated NaN in features")
        n = len(X)
        return np.full(n, self.prediction), np.full(n, self.confidence)


def test_stale_confidence_is_not_reported_after_a_failed_prediction():
    """Regression test: PredictProbaAdapter.last_confidence used to only be
    set on success, so a HOLD caused by a prediction exception could be
    journaled alongside a stale, misleading confidence from a prior cycle."""
    agent = PredictionAgent(auto_load=False)
    ensemble = FlakyEnsemble(prediction=0.9, confidence=0.8)
    agent.set_model("BTC/USDT", ensemble, feature_columns=["close"])

    first = agent.predict("BTC/USDT", _features())
    assert first.signal == Signal.BUY
    assert first.confidence == 0.8

    second = agent.predict("BTC/USDT", _features())  # ensemble raises on this call
    assert second.signal == Signal.HOLD
    assert second.confidence is None  # must not be the stale 0.8 from the first call


def test_auto_loaded_model_retains_feature_columns_from_promotion(tmp_path):
    """Regression: save_model used to persist only the bare model object, so
    a symbol whose model was promoted in one process (setting
    feature_columns in memory) but auto-loaded fresh in another - e.g. after
    a restart with no immediate retrain for that symbol - would silently get
    feature_columns=None and crash (or worse, silently mispredict) on the
    very first live predict() call. feature_columns must now survive the
    round trip without any manual set_feature_columns fixup."""
    trainer = PredictionAgent(model_dir=tmp_path, auto_load=False)
    trainer.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

    fresh_agent = PredictionAgent(model_dir=tmp_path, auto_load=True)
    result = fresh_agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.BUY
    assert fresh_agent._get_or_create_strategy("BTC/USDT").feature_columns == ["close"]


def test_a_model_promoted_on_an_older_smaller_feature_set_survives_new_columns_being_added(tmp_path):
    """The critical safety property behind ever adding new columns to
    agents.features.FEATURE_COLUMNS (e.g. the Smart Money Concepts
    features): a model promoted BEFORE the new columns existed stores
    its OWN feature_columns list at promotion time (see save_model's
    bundling), and AIStrategy slices the live features DataFrame down to
    exactly that list before predicting - so a real, currently-live model
    trained on the smaller, older feature set must keep predicting
    correctly even once the live feature DataFrame grows to include
    brand new columns it was never trained on and knows nothing about."""
    from trading_assistant.agents.features import FEATURE_COLUMNS

    old_feature_columns = [
        c
        for c in FEATURE_COLUMNS
        if c not in ("smc_structure_state", "smc_bos", "smc_mss", "price_vs_session_vwap")
    ]
    trainer = PredictionAgent(model_dir=tmp_path, auto_load=False)
    trainer.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=old_feature_columns)

    # The CURRENT, full-width live feature frame - as if a live cycle ran
    # build_features() today, well after the model above was promoted.
    live_features = pd.DataFrame({col: [1.0, 2.0, 3.0] for col in FEATURE_COLUMNS})

    fresh_agent = PredictionAgent(model_dir=tmp_path, auto_load=True)
    result = fresh_agent.predict("BTC/USDT", live_features)

    assert result.signal == Signal.BUY  # never raised, never silently misfired


def test_load_model_restores_feature_columns_too(tmp_path):
    trainer = PredictionAgent(model_dir=tmp_path, auto_load=False)
    trainer.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

    fresh_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    fresh_agent.load_model("BTC/USDT")

    assert fresh_agent._get_or_create_strategy("BTC/USDT").feature_columns == ["close"]
    result = fresh_agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.BUY


def test_auto_load_falls_back_gracefully_for_old_pre_bundle_model_files(tmp_path):
    """Model files saved before this fix exist on disk as the bare model
    object, not the {"model", "feature_columns"} bundle - loading one must
    not crash, just fall back to feature_columns=None like before."""
    import joblib

    old_style_path = tmp_path / "BTC_USDT.joblib"
    joblib.dump(DummyClassifier(up_probability=0.9), old_style_path)

    agent = PredictionAgent(model_dir=tmp_path, auto_load=True)
    agent.set_feature_columns("BTC/USDT", ["close"])  # still required for pre-fix files, as before

    result = agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.BUY


def test_promote_model_atomically_swaps_model_and_feature_columns(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
    old_strategy = agent._get_or_create_strategy("BTC/USDT")

    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])
    new_strategy = agent._get_or_create_strategy("BTC/USDT")

    assert new_strategy is not old_strategy  # swapped wholesale, not mutated in place
    result = agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.BUY
    assert (tmp_path / "BTC_USDT.joblib").exists()  # also persisted to disk


def test_promote_model_keeps_one_prior_version_on_disk(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])

    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

    assert (tmp_path / "BTC_USDT.previous.joblib").exists()


def test_rollback_model_reverts_to_the_prior_version(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

    rolled_back = agent.rollback_model("BTC/USDT")

    assert rolled_back is True
    result = agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.SELL  # back to the 0.1-probability model, not the 0.9 one


def test_rollback_model_persists_so_a_restart_keeps_the_reverted_model(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])
    agent.rollback_model("BTC/USDT")

    fresh_agent = PredictionAgent(model_dir=tmp_path, auto_load=True)
    result = fresh_agent.predict("BTC/USDT", _features())

    assert result.signal == Signal.SELL


def test_rollback_model_is_a_noop_with_no_prior_version(tmp_path):
    agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent.promote_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])  # only ever promoted once

    rolled_back = agent.rollback_model("BTC/USDT")

    assert rolled_back is False
    result = agent.predict("BTC/USDT", _features())
    assert result.signal == Signal.BUY  # unaffected - still the only model that ever existed
