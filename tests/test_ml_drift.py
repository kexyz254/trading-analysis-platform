import numpy as np
import pandas as pd
import pytest

from trading_assistant.ml.drift import (
    compute_baseline_distribution,
    compute_bucket_boundaries,
    compute_feature_drift,
    compute_feature_drift_from_baseline,
    drift_severity,
    population_stability_index,
    population_stability_index_from_baseline,
    summarize_drift,
)


def _series(values):
    return pd.Series(values, dtype=float)


class TestComputeBucketBoundaries:
    def test_returns_deciles_for_ample_data(self):
        baseline = _series(np.arange(1000))
        boundaries = compute_bucket_boundaries(baseline, num_buckets=10)
        assert len(boundaries) == 11
        assert boundaries[0] < boundaries[-1]

    def test_insufficient_data_returns_empty(self):
        baseline = _series([1.0, 2.0, 3.0])
        assert compute_bucket_boundaries(baseline, num_buckets=10) == []

    def test_constant_feature_returns_empty(self):
        baseline = _series([5.0] * 500)
        assert compute_bucket_boundaries(baseline, num_buckets=10) == []

    def test_drops_nans_before_bucketing(self):
        baseline = pd.Series([np.nan] * 5 + list(range(500)), dtype=float)
        boundaries = compute_bucket_boundaries(baseline, num_buckets=10)
        assert len(boundaries) == 11


class TestPopulationStabilityIndex:
    def test_identical_distributions_score_near_zero(self):
        rng = np.random.default_rng(42)
        baseline = _series(rng.normal(0, 1, 2000))
        current = _series(rng.normal(0, 1, 2000))
        psi = population_stability_index(baseline, current)
        assert psi is not None
        assert psi < 0.10

    def test_clearly_shifted_distribution_scores_significant(self):
        rng = np.random.default_rng(7)
        baseline = _series(rng.normal(0, 1, 2000))
        current = _series(rng.normal(6, 1, 500))  # totally different location
        psi = population_stability_index(baseline, current)
        assert psi is not None
        assert psi >= 0.25

    def test_insufficient_current_data_returns_none(self):
        baseline = _series(np.arange(500))
        current = _series([])
        assert population_stability_index(baseline, current) is None

    def test_insufficient_baseline_data_returns_none(self):
        baseline = _series([1.0, 2.0])
        current = _series(np.arange(500))
        assert population_stability_index(baseline, current) is None

    def test_constant_baseline_returns_none(self):
        baseline = _series([3.0] * 500)
        current = _series(np.arange(500))
        assert population_stability_index(baseline, current) is None

    def test_current_values_outside_baseline_range_still_computed(self):
        baseline = _series(np.arange(500))
        current = _series(np.arange(10000, 10500))  # entirely outside baseline's range
        psi = population_stability_index(baseline, current)
        assert psi is not None
        assert psi > 0


class TestDriftSeverity:
    def test_none_psi_is_unknown(self):
        assert drift_severity(None) == "unknown"

    def test_low_psi_is_none_severity(self):
        assert drift_severity(0.02) == "none"

    def test_moderate_boundary(self):
        assert drift_severity(0.10) == "moderate"
        assert drift_severity(0.24) == "moderate"

    def test_significant_boundary(self):
        assert drift_severity(0.25) == "significant"
        assert drift_severity(1.5) == "significant"


class TestComputeFeatureDrift:
    def test_per_feature_psi_dict(self):
        rng = np.random.default_rng(1)
        baseline = pd.DataFrame({"a": rng.normal(0, 1, 1000), "b": rng.normal(0, 1, 1000)})
        current = pd.DataFrame({"a": rng.normal(0, 1, 500), "b": rng.normal(5, 1, 500)})
        result = compute_feature_drift(baseline, current, ["a", "b"])
        assert set(result.keys()) == {"a", "b"}
        assert result["b"] > result["a"]

    def test_ignores_columns_missing_from_either_frame(self):
        baseline = pd.DataFrame({"a": np.arange(500)})
        current = pd.DataFrame({"a": np.arange(500)})
        result = compute_feature_drift(baseline, current, ["a", "missing_col"])
        assert list(result.keys()) == ["a"]


class TestComputeBaselineDistribution:
    def test_returns_boundaries_and_matching_baseline_pct(self):
        baseline = _series(np.arange(1000))
        dist = compute_baseline_distribution(baseline, num_buckets=10)
        assert dist is not None
        assert len(dist["boundaries"]) == 11
        assert len(dist["baseline_pct"]) == 10
        assert sum(dist["baseline_pct"]) == pytest.approx(1.0)

    def test_none_when_not_enough_spread(self):
        baseline = _series([5.0] * 500)
        assert compute_baseline_distribution(baseline) is None

    def test_comparing_the_exact_same_data_scores_near_zero_even_with_uneven_buckets(self):
        # A discrete/cyclical-style feature (few repeated values) forces
        # compute_bucket_boundaries to dedupe down to unevenly-populated
        # buckets - the baseline_pct must reflect that real occupancy, not
        # an assumed-uniform 1/num_buckets, or comparing identical data
        # against itself would falsely register as drifted.
        values = ([0.0] * 60) + ([0.5] * 90) + ([1.0] * 121)
        baseline = _series(values)
        dist = compute_baseline_distribution(baseline, num_buckets=10)
        assert dist is not None
        psi = population_stability_index_from_baseline(dist, baseline)
        assert psi is not None
        assert psi < 0.01


class TestPopulationStabilityIndexFromBaseline:
    def test_matches_full_computation_reasonably_closely(self):
        rng = np.random.default_rng(3)
        baseline = _series(rng.normal(0, 1, 5000))
        current = _series(rng.normal(2, 1, 1000))
        dist = compute_baseline_distribution(baseline, num_buckets=10)
        psi_from_baseline = population_stability_index_from_baseline(dist, current)
        psi_full = population_stability_index(baseline, current)
        assert psi_from_baseline is not None
        assert psi_full is not None
        assert drift_severity(psi_from_baseline) == drift_severity(psi_full)

    def test_empty_baseline_distribution_returns_none(self):
        assert population_stability_index_from_baseline({}, _series(np.arange(100))) is None
        assert population_stability_index_from_baseline(None, _series(np.arange(100))) is None

    def test_empty_current_returns_none(self):
        dist = {"boundaries": [0.0, 1.0, 2.0, 3.0], "baseline_pct": [0.34, 0.33, 0.33]}
        assert population_stability_index_from_baseline(dist, _series([])) is None


class TestComputeFeatureDriftFromBaseline:
    def test_per_feature_dict_from_persisted_baseline(self):
        rng = np.random.default_rng(9)
        baseline_distributions = {
            "a": compute_baseline_distribution(_series(rng.normal(0, 1, 2000)), num_buckets=10),
        }
        current = pd.DataFrame({"a": rng.normal(0, 1, 500)})
        result = compute_feature_drift_from_baseline(baseline_distributions, current, ["a", "missing"])
        assert list(result.keys()) == ["a"]
        assert result["a"] is not None


class TestSummarizeDrift:
    def test_empty_when_nothing_computed(self):
        summary = summarize_drift({"a": None, "b": None})
        assert summary["overall_severity"] == "unknown"
        assert summary["worst_features"] == []

    def test_worst_feature_drives_overall_severity(self):
        summary = summarize_drift({"a": 0.02, "b": 0.31, "c": 0.15})
        assert summary["overall_severity"] == "significant"
        assert summary["worst_features"][0] == {"feature": "b", "psi": 0.31}

    def test_worst_features_sorted_descending_and_capped_at_five(self):
        feature_psi = {f"f{i}": float(i) / 10 for i in range(8)}
        summary = summarize_drift(feature_psi)
        psis = [entry["psi"] for entry in summary["worst_features"]]
        assert psis == sorted(psis, reverse=True)
        assert len(summary["worst_features"]) == 5
