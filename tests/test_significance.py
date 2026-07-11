from trading_assistant.validation.significance import (
    baseline_p_value,
    beats_baseline_significantly,
    benjamini_hochberg,
    clears_threshold_significantly,
    threshold_p_value,
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


class TestThresholdPValue:
    def test_none_when_too_few_folds(self):
        assert threshold_p_value([1.0, 1.2, 0.9], threshold=0.5) is None

    def test_real_p_value_when_enough_folds(self):
        folds = [1.5, 1.6, 1.4, 1.55, 1.45, 1.5, 1.6]
        p = threshold_p_value(folds, threshold=0.5)
        assert p is not None
        assert 0.0 <= p <= 1.0
        # A clearly-and-consistently-above-threshold sample must produce
        # a small p-value - same underlying case
        # clears_threshold_significantly's own test asserts is significant.
        assert p < 0.10

    def test_agrees_with_clears_threshold_significantly_at_the_same_confidence(self):
        folds = [0.55, 0.45, 0.52, 0.48, 0.51, 0.49, 0.53]
        p = threshold_p_value(folds, threshold=0.5)
        assert p is not None
        assert (p < 0.10) == clears_threshold_significantly(folds, threshold=0.5)


class TestBaselinePValue:
    def test_none_when_too_few_folds(self):
        assert baseline_p_value([1.0, 1.2], [0.5, 0.6]) is None

    def test_none_when_either_side_empty(self):
        assert baseline_p_value([], [0.5, 0.6, 0.7, 0.8, 0.9]) is None
        assert baseline_p_value([0.5, 0.6, 0.7, 0.8, 0.9], []) is None

    def test_real_p_value_when_enough_folds(self):
        candidate = [1.5, 1.6, 1.55, 1.65, 1.5, 1.58]
        baseline = [0.5, 0.6, 0.55, 0.45, 0.52, 0.48]
        p = baseline_p_value(candidate, baseline)
        assert p is not None
        assert p < 0.10  # matches the "clearly better" case being significant


class TestBenjaminiHochberg:
    def test_empty_input_returns_empty(self):
        assert benjamini_hochberg([]) == []

    def test_all_none_is_never_significant(self):
        assert benjamini_hochberg([None, None, None]) == [False, False, False]

    def test_single_small_p_value_below_alpha_is_significant(self):
        assert benjamini_hochberg([0.01], alpha=0.10) == [True]

    def test_single_large_p_value_above_alpha_is_not_significant(self):
        assert benjamini_hochberg([0.5], alpha=0.10) == [False]

    def test_none_entries_excluded_from_the_family_and_never_significant(self):
        # The two None entries must not count toward the family size used
        # to correct the two real p-values, and must themselves always
        # come back False regardless of how small a neighboring p-value is.
        result = benjamini_hochberg([0.01, None, 0.02, None], alpha=0.10)
        assert result == [True, False, True, False]

    def test_corrects_more_conservatively_than_the_raw_per_test_alpha(self):
        """The core regression this exists to prevent: two borderline
        p-values (0.08, 0.09) each individually clear the raw per-test
        alpha (0.10) - a naive check would call both significant. But
        with a 5th, clearly-noise p-value (0.15) in the same family, BH's
        rank-based thresholds (0.02, 0.04, 0.06, 0.08, 0.10 for m=5) mean
        neither 0.08 (needs <=0.06 at its rank) nor 0.09 (needs <=0.08)
        clears its own threshold, and the worse 0.15 doesn't save them
        either (unlike if everything in the family were already below the
        raw alpha, where the top rank trivially self-validates) - so both
        are correctly demoted from "significant" to "not enough evidence,"
        exactly the false-champion promotions from sampling noise the
        module docstring warns a fixed per-test alpha will eventually
        produce as the tournament's family of tests grows."""
        p_values = [0.001, 0.001, 0.08, 0.09, 0.15]
        naive_significant = [p < 0.10 for p in p_values]
        corrected = benjamini_hochberg(p_values, alpha=0.10)

        assert naive_significant == [True, True, True, True, False]  # naive waves 4 through
        assert corrected == [True, True, False, False, False]  # BH correctly keeps only the 2 real signals
        assert sum(corrected) < sum(naive_significant)
