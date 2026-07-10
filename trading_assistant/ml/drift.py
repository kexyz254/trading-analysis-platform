"""Feature drift detection via Population Stability Index (PSI) - the
standard, interpretable metric for "how much has a feature's distribution
shifted" used throughout ML-in-production monitoring (originating in
credit-risk model monitoring, not invented for this system).

Nothing in this system previously checked whether LIVE feature
distributions were still representative of what a promoted model actually
trained on. A model can silently decay as market conditions shift (a
regime change, a newly-listed symbol's early volatility settling down,
liquidity conditions changing) with no signal until a scheduled retrain
eventually happens to catch it - which, at this system's default
retrain_interval_hours, could be many hours away.

Conventional PSI thresholds (widely used, not tuned for this system
specifically):
  PSI < 0.10             : no significant shift
  0.10 <= PSI < 0.25      : moderate shift, worth watching
  PSI >= 0.25             : significant shift - the training distribution
                            may no longer represent what the model is
                            actually seeing live
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

PSI_MODERATE_THRESHOLD = 0.10
PSI_SIGNIFICANT_THRESHOLD = 0.25


def compute_bucket_boundaries(baseline: pd.Series, num_buckets: int = 10) -> List[float]:
    """Quantile boundaries derived from the BASELINE distribution only -
    both baseline and current populations get bucketed against these same
    boundaries, which is what makes PSI a meaningful "did the distribution
    shift" comparison rather than two independent histograms with no
    shared reference frame. Returns [] if there isn't enough baseline data
    to bucket meaningfully, or the baseline has no spread (e.g. a constant
    feature - PSI isn't a meaningful question for it)."""
    clean = baseline.dropna()
    if len(clean) < num_buckets:
        return []
    quantiles = np.linspace(0, 1, num_buckets + 1)
    raw_boundaries = clean.quantile(quantiles).tolist()

    # Guard against floating-point noise on a near-constant feature (e.g. a
    # bounded oscillator that's pinned to one value for an entire training
    # window) manufacturing spurious "distinct" quantile boundaries that
    # differ only in the last few bits of precision. PSI bucketed that
    # finely would register huge drift purely from ordinary floating-point
    # rounding noise between two otherwise-identical computations - a false
    # alarm, not a real distribution shift.
    spread = raw_boundaries[-1] - raw_boundaries[0]
    scale = max(abs(clean.median()), 1e-12)
    if spread / scale < 1e-9:
        return []

    boundaries = sorted(set(round(b, 10) for b in raw_boundaries))
    if len(boundaries) < 2:
        return []
    return boundaries


def population_stability_index(baseline: pd.Series, current: pd.Series, num_buckets: int = 10) -> Optional[float]:
    """PSI between a baseline (e.g. training-time) and current (e.g. live)
    distribution of the same feature. None if there isn't enough data to
    bucket meaningfully - "can't compute yet" is a distinct, honest state
    from "computed and found stable", and must never be silently treated
    as either "no drift" or "drifted"."""
    baseline_clean = baseline.dropna()
    current_clean = current.dropna()
    if len(current_clean) < 1:
        return None

    boundaries = compute_bucket_boundaries(baseline_clean, num_buckets)
    if len(boundaries) < 2:
        return None

    # Extend the outer edges to +-inf so any current value outside the
    # baseline's observed range still lands in the nearest bucket instead
    # of being silently dropped from the comparison (which would understate
    # drift precisely when a live value moves somewhere training never saw).
    edges = list(boundaries)
    edges[0] = -np.inf
    edges[-1] = np.inf

    baseline_counts = pd.cut(baseline_clean, bins=edges, include_lowest=True).value_counts(sort=False)
    current_counts = pd.cut(current_clean, bins=edges, include_lowest=True).value_counts(sort=False)

    # A zero-occupancy bucket makes the PSI log term undefined - clip to a
    # small floor rather than dropping the bucket, so a feature that
    # completely vacated a bucket the baseline had mass in still registers
    # as drift instead of silently being excluded from the sum.
    baseline_pct = (baseline_counts / len(baseline_clean)).clip(lower=1e-4)
    current_pct = (current_counts / len(current_clean)).clip(lower=1e-4)

    psi = float(((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)).sum())
    return psi


def drift_severity(psi: Optional[float]) -> str:
    """'unknown' | 'none' | 'moderate' | 'significant'."""
    if psi is None:
        return "unknown"
    if psi >= PSI_SIGNIFICANT_THRESHOLD:
        return "significant"
    if psi >= PSI_MODERATE_THRESHOLD:
        return "moderate"
    return "none"


def compute_feature_drift(
    baseline: pd.DataFrame, current: pd.DataFrame, feature_columns: List[str], num_buckets: int = 10
) -> Dict[str, Optional[float]]:
    """PSI per feature - a per-feature breakdown, not one averaged number,
    since a single badly-drifted feature can matter even while most others
    stay stable, and an average would hide exactly that."""
    return {
        col: population_stability_index(baseline[col], current[col], num_buckets)
        for col in feature_columns
        if col in baseline.columns and col in current.columns
    }


def compute_baseline_distribution(baseline: pd.Series, num_buckets: int = 10) -> Optional[Dict[str, list]]:
    """A compact, persistable summary of a feature's baseline distribution:
    its equal-frequency bucket boundaries AND each bucket's actual
    occupancy. The occupancy is NOT assumed uniform (1/num_buckets), even
    though the boundaries come from quantiles: repeated/tied quantile
    values - common for discrete or cyclical features like hour-of-day, or
    any feature with a handful of common values - make `compute_bucket_boundaries`
    dedupe down to fewer, UNEVENLY populated buckets. Assuming uniform
    occupancy in that case manufactures phantom drift purely from bucketing
    artifacts, not any real distribution shift - comparing the exact same
    data against itself would falsely score as "significant" drift.
    Returns None if there isn't enough baseline data/spread to bucket."""
    boundaries = compute_bucket_boundaries(baseline, num_buckets)
    if len(boundaries) < 2:
        return None
    clean = baseline.dropna()
    edges = list(boundaries)
    edges[0] = -np.inf
    edges[-1] = np.inf
    counts = pd.cut(clean, bins=edges, include_lowest=True).value_counts(sort=False)
    baseline_pct = (counts / len(clean)).tolist()
    return {"boundaries": boundaries, "baseline_pct": baseline_pct}


def population_stability_index_from_baseline(
    baseline_distribution: Dict[str, list], current: pd.Series
) -> Optional[float]:
    """PSI against a PERSISTED baseline distribution (see
    compute_baseline_distribution) rather than the original training rows,
    which by the time this runs live aren't worth carrying around in full
    just to monitor drift."""
    if not baseline_distribution:
        return None
    boundaries = baseline_distribution.get("boundaries") or []
    baseline_pct_list = baseline_distribution.get("baseline_pct") or []
    current_clean = current.dropna()
    if len(current_clean) < 1 or len(boundaries) < 2 or len(baseline_pct_list) != len(boundaries) - 1:
        return None

    edges = list(boundaries)
    edges[0] = -np.inf
    edges[-1] = np.inf

    current_counts = pd.cut(current_clean, bins=edges, include_lowest=True).value_counts(sort=False)
    current_pct = (current_counts / len(current_clean)).clip(lower=1e-4)
    # pd.cut's category order always follows bin (left-to-right) order
    # regardless of value_counts(sort=False), matching the order
    # baseline_pct_list was built in - safe to zip by position.
    baseline_pct = pd.Series(baseline_pct_list, index=current_pct.index).clip(lower=1e-4)

    psi = float(((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)).sum())
    return psi


def compute_feature_drift_from_baseline(
    baseline_distributions: Dict[str, Dict[str, list]], current: pd.DataFrame, feature_columns: List[str]
) -> Dict[str, Optional[float]]:
    """Same idea as compute_feature_drift, but against persisted baseline
    distributions (see population_stability_index_from_baseline) rather
    than a live DataFrame of the original training rows."""
    return {
        col: population_stability_index_from_baseline(baseline_distributions[col], current[col])
        for col in feature_columns
        if col in baseline_distributions and col in current.columns
    }


def summarize_drift(feature_psi: Dict[str, Optional[float]]) -> Dict[str, object]:
    """A compact, loggable summary: worst-drifted features first, overall
    severity taken from the SINGLE worst feature (not an average - one
    badly-drifted feature is exactly the kind of thing an average across
    30 features would dilute into invisibility)."""
    computed = {k: v for k, v in feature_psi.items() if v is not None}
    if not computed:
        return {"overall_severity": "unknown", "worst_features": [], "num_features_checked": len(feature_psi)}
    worst = sorted(computed.items(), key=lambda kv: kv[1], reverse=True)
    worst_psi = worst[0][1]
    return {
        "overall_severity": drift_severity(worst_psi),
        "worst_features": [{"feature": name, "psi": round(psi, 4)} for name, psi in worst[:5]],
        "num_features_checked": len(feature_psi),
        "num_features_computed": len(computed),
    }
