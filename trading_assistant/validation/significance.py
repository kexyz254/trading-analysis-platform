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

from typing import List

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


def clears_threshold_significantly(
    fold_sharpes: List[float], threshold: float, confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
) -> bool:
    """Whether fold-level Sharpe ratios are significantly ABOVE
    `threshold` (not just their median), via a one-sided Wilcoxon
    signed-rank test on (fold_sharpe - threshold)."""
    if len(fold_sharpes) < MIN_FOLDS_FOR_TEST:
        return bool(_median(fold_sharpes) > threshold)
    diffs = [s - threshold for s in fold_sharpes]
    if all(d == 0 for d in diffs):
        return False
    try:
        _, p_value = stats.wilcoxon(diffs, alternative="greater")
    except ValueError:
        # Every diff has the same sign and lands on ties wilcoxon can't
        # rank - a completely one-sided sample is itself already strong
        # evidence either way.
        return all(d > 0 for d in diffs)
    # bool(...): scipy returns numpy.bool_, not a native bool - left
    # unwrapped, `result is True/False` silently fails (different object
    # identity) for any caller that checks it that way, and it serializes
    # oddly to JSON.
    return bool(p_value < (1 - confidence_level))


def beats_baseline_significantly(
    candidate_fold_sharpes: List[float],
    baseline_fold_sharpes: List[float],
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> bool:
    """Whether the candidate's fold-level Sharpe ratios are significantly
    higher than the baseline's, via a one-sided Mann-Whitney U test - two
    independent samples, no assumption they're paired or normally
    distributed."""
    if not candidate_fold_sharpes or not baseline_fold_sharpes:
        return False  # nothing to compare against is not evidence of beating it
    if len(candidate_fold_sharpes) < MIN_FOLDS_FOR_TEST or len(baseline_fold_sharpes) < MIN_FOLDS_FOR_TEST:
        return bool(_median(candidate_fold_sharpes) > _median(baseline_fold_sharpes))
    try:
        _, p_value = stats.mannwhitneyu(candidate_fold_sharpes, baseline_fold_sharpes, alternative="greater")
    except ValueError:
        return False
    return bool(p_value < (1 - confidence_level))
