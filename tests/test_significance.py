from trading_assistant.validation.significance import (
    beats_baseline_significantly,
    clears_threshold_significantly,
)


class TestClearsThresholdSignificantly:
    def test_small_sample_falls_back_to_median_comparison(self):
        assert clears_threshold_significantly([1.0, 1.2, 0.9], threshold=0.5) is True
        assert clears_threshold_significantly([0.3, 0.4, 0.2], threshold=0.5) is False

    def test_clearly_and_consistently_above_threshold_is_significant(self):
        folds = [1.5, 1.6, 1.4, 1.55, 1.45, 1.5, 1.6]
        assert clears_threshold_significantly(folds, threshold=0.5) is True

    def test_barely_and_inconsistently_above_threshold_is_not_significant(self):
        # median just clears 0.5, but half the folds are actually below it -
        # exactly the "beat by noise" case this test exists to catch
        folds = [0.55, 0.45, 0.52, 0.48, 0.51, 0.49, 0.53]
        assert clears_threshold_significantly(folds, threshold=0.5) is False

    def test_clearly_below_threshold_is_not_significant(self):
        folds = [0.1, 0.2, 0.05, 0.15, 0.1, 0.12, 0.08]
        assert clears_threshold_significantly(folds, threshold=0.5) is False

    def test_empty_folds_is_not_significant(self):
        assert clears_threshold_significantly([], threshold=0.5) is False

    def test_all_folds_exactly_at_threshold_is_not_significant(self):
        assert clears_threshold_significantly([0.5, 0.5, 0.5, 0.5, 0.5], threshold=0.5) is False


class TestBeatsBaselineSignificantly:
    def test_small_sample_falls_back_to_median_comparison(self):
        assert beats_baseline_significantly([1.0, 1.2], [0.5, 0.6]) is True
        assert beats_baseline_significantly([0.5, 0.4], [0.9, 1.0]) is False

    def test_clearly_and_consistently_better_is_significant(self):
        candidate = [1.5, 1.6, 1.55, 1.65, 1.5, 1.58]
        baseline = [0.5, 0.6, 0.55, 0.45, 0.52, 0.48]
        assert beats_baseline_significantly(candidate, baseline) is True

    def test_marginally_better_median_with_heavy_overlap_is_not_significant(self):
        # candidate's median edges out the baseline's, but the two
        # distributions overlap heavily - exactly the "any positive delta
        # counts as beating it" trap a raw comparison would fall into
        candidate = [0.5, 0.6, 0.4, 0.55, 0.45, 0.58]
        baseline = [0.48, 0.5, 0.42, 0.52, 0.44, 0.5]
        assert beats_baseline_significantly(candidate, baseline) is False

    def test_worse_candidate_is_not_significant(self):
        candidate = [0.2, 0.1, 0.15, 0.05, 0.12, 0.18]
        baseline = [0.8, 0.9, 0.85, 0.75, 0.82, 0.88]
        assert beats_baseline_significantly(candidate, baseline) is False

    def test_empty_either_side_is_not_significant(self):
        assert beats_baseline_significantly([], [0.5, 0.6]) is False
        assert beats_baseline_significantly([0.5, 0.6], []) is False
