"""Statistical significance for promotion decisions.

Walk-forward validation already checks a candidate against fixed
performance thresholds and against the previously-promoted baseline's
Sharpe - but a raw comparison of two point estimates ("beat the baseline's
median Sharpe by ANY margin") treats "beat by 0.001" identically to "beat
by 2.0". Repeated across many candidates, symbols, and regimes on a
recurring cadence - this system's own multiple-testing exposure, since the
tournament reruns hourly - that comparison will eventually promote
something on pure sampling noise. These functions ask instead: given the
actual spread across walk-forward folds, can we be reasonably confident
the difference is real, not just a lucky draw of folds?

Both tests are non-parametric (Wilcoxon signed-rank / Mann-Whitney U) -
deliberately not a t-test, which assumes roughly-normal Sharpe ratios;
a handful of walk-forward folds is exactly the small, non-normal sample
size where that assumption is least trustworthy.
"""

from __future__ import annotations

from typing import List, Optional

from scipy import stats

DEFAULT_CONFIDENCE_LEVEL = 0.90
# Below this many folds, a rank-based test has too little power to say
# anything - falls back to a plain median comparison, an honest "not
# enough evidence for statistics yet" rather than a fabricated p-value.
MIN_FOLDS_FOR_TEST = 5


def _median(values: List[float]) -> float:
    if not values:
        return float("-inf")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def threshold_p_value(fold_sharpes: List[float], threshold: float) -> Optional[float]:
    """Raw one-sided Wilcoxon signed-rank p-value for fold-level Sharpe
    ratios being above `threshold` - the building block both
    clears_threshold_significantly (a single, uncorrected test) and
    benjamini_hochberg (correcting across a whole FAMILY of these tests -
    see its own docstring) are built on. None when there aren't enough
    folds for a real test (MIN_FOLDS_FOR_TEST) - callers fall back to a
    plain point-estimate comparison instead, which has no p-value to
    correct and so must never be included in a corrected family."""
    if len(fold_sharpes) < MIN_FOLDS_FOR_TEST:
        return None
    diffs = [s - threshold for s in fold_sharpes]
    if all(d == 0 for d in diffs):
        return 1.0
    try:
        _, p_value = stats.wilcoxon(diffs, alternative="greater")
    except ValueError:
        # Every diff has the same sign and lands on ties wilcoxon can't
        # rank - a completely one-sided sample is itself already strong
        # evidence either way.
        return 0.0 if all(d > 0 for d in diffs) else 1.0
    return float(p_value)


def clears_threshold_significantly(
    fold_sharpes: List[float], threshold: float, confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
) -> bool:
    """Whether fold-level Sharpe ratios are significantly ABOVE
    `threshold` (not just their median), via a one-sided Wilcoxon
    signed-rank test on (fold_sharpe - threshold). A single, UNCORRECTED
    test at a fixed per-test alpha - see benjamini_hochberg for the
    multi-symbol/multi-candidate corrected equivalent, which is what a
    tournament reran on a recurring cadence across many candidates should
    actually use (see module docstring)."""
    p_value = threshold_p_value(fold_sharpes, threshold)
    if p_value is None:
        return bool(_median(fold_sharpes) > threshold)
    # bool(...): scipy returns numpy.bool_, not a native bool - left
    # unwrapped, `result is True/False` silently fails (different object
    # identity) for any caller that checks it that way, and it serializes
    # oddly to JSON.
    return bool(p_value < (1 - confidence_level))


def baseline_p_value(candidate_fold_sharpes: List[float], baseline_fold_sharpes: List[float]) -> Optional[float]:
    """Raw one-sided Mann-Whitney U p-value for the candidate's fold-level
    Sharpe ratios being higher than the baseline's - two independent
    samples, no assumption they're paired or normally distributed. None
    when either sample is empty/too small for a real test, or on a
    degenerate sample scipy can't rank (all-identical values) - callers
    fall back to a plain point-estimate comparison, which has no p-value
    to correct."""
    if not candidate_fold_sharpes or not baseline_fold_sharpes:
        return None
    if len(candidate_fold_sharpes) < MIN_FOLDS_FOR_TEST or len(baseline_fold_sharpes) < MIN_FOLDS_FOR_TEST:
        return None
    try:
        _, p_value = stats.mannwhitneyu(candidate_fold_sharpes, baseline_fold_sharpes, alternative="greater")
    except ValueError:
        return None
    return float(p_value)


def beats_baseline_significantly(
    candidate_fold_sharpes: List[float],
    baseline_fold_sharpes: List[float],
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> bool:
    """Whether the candidate's fold-level Sharpe ratios are significantly
    higher than the baseline's. A single, UNCORRECTED test at a fixed
    per-test alpha - see benjamini_hochberg for the corrected equivalent."""
    if not candidate_fold_sharpes or not baseline_fold_sharpes:
        return False  # nothing to compare against is not evidence of beating it
    p_value = baseline_p_value(candidate_fold_sharpes, baseline_fold_sharpes)
    if p_value is None:
        if len(candidate_fold_sharpes) < MIN_FOLDS_FOR_TEST or len(baseline_fold_sharpes) < MIN_FOLDS_FOR_TEST:
            return bool(_median(candidate_fold_sharpes) > _median(baseline_fold_sharpes))
        return False  # a genuine degenerate-sample ValueError - not evidence of beating it
    return bool(p_value < (1 - confidence_level))


def benjamini_hochberg(p_values: List[Optional[float]], alpha: float = 1 - DEFAULT_CONFIDENCE_LEVEL) -> List[bool]:
    """Benjamini-Hochberg step-up procedure for controlling the false
    discovery rate across a FAMILY of simultaneous hypothesis tests - see
    this module's own docstring on why a fixed per-test alpha, applied to
    every candidate/symbol/regime combination the tournament retests on a
    recurring hourly cadence, will eventually promote something on pure
    sampling noise as the number of tests grows.

    Sort p-values ascending as p(1) <= p(2) <= ... <= p(m); find the
    largest rank k where p(k) <= (k/m)*alpha; every test at or below that
    rank is significant after correction. This is uniformly LESS
    permissive than testing each one at the same raw alpha, which is the
    whole point - it trades a few more missed real edges for a much lower
    rate of promoting noise as false champions.

    None entries (no p-value - too few folds for a real test, see
    threshold_p_value/baseline_p_value) are excluded from the corrected
    family entirely and always come back False here; the caller is
    expected to fall back to that test's own point-estimate comparison
    instead, exactly as the uncorrected single-test functions above do.
    """
    m = len(p_values)
    significant = [False] * m
    testable = [(i, p) for i, p in enumerate(p_values) if p is not None]
    family_size = len(testable)
    if family_size == 0:
        return significant
    testable.sort(key=lambda pair: pair[1])
    largest_k = 0
    for rank, (_, p_value) in enumerate(testable, start=1):
        if p_value <= (rank / family_size) * alpha:
            largest_k = rank
    for rank, (original_index, _) in enumerate(testable, start=1):
        if rank <= largest_k:
            significant[original_index] = True
    return significant
