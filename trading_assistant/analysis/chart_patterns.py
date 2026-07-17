"""Classical chart-pattern detection on OHLCV frames.

Detects the standard technical-analysis patterns (triangles, wedges,
rectangles, flags, head-and-shoulders, double/triple tops and bottoms,
cup-and-handle) from confirmed pivot points, and exposes them two ways:

- detect_patterns(frame): what is present at the RIGHT EDGE of the chart,
  with drawable trendline segments (extended a few bars forward) - for
  display.
- pattern_feature_frame(frame): one column per pattern, one row per bar,
  value = detection confidence (0 when absent) - a feature-extraction
  layer for models to train on and, via out-of-sample evaluation, to
  measure whether these patterns actually carry predictive information.

No look-ahead anywhere: a pivot high at bar p needs `right` lower bars
after it to exist, so it only becomes usable from bar p+right onward -
pattern features at bar i are computed strictly from pivots already
confirmed by bar i. Confidence is GEOMETRY quality (trendline fit,
touch count, rule margins), deliberately not a claim about what price
does next - whether a clean pattern predicts anything is exactly what
the downstream model evaluation exists to answer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PATTERN_NAMES = [
    "ascending_triangle",
    "descending_triangle",
    "symmetric_triangle",
    "rising_wedge",
    "falling_wedge",
    "bullish_rectangle",
    "bearish_rectangle",
    "bull_flag",
    "bear_flag",  # a.k.a. inverted flag
    "head_shoulders",
    "inverse_head_shoulders",
    "double_top",
    "double_bottom",
    "triple_bottom",
    "cup_handle",
]

PATTERN_COLUMNS = [f"pat_{name}" for name in PATTERN_NAMES]

# Conventional directional bias of each pattern (context-free reading;
# rectangles/flags are continuation patterns whose bias comes from the
# trend/pole direction encoded in their own detection rules).
PATTERN_BIAS = {
    "ascending_triangle": "bullish",
    "descending_triangle": "bearish",
    "symmetric_triangle": "neutral",
    "rising_wedge": "bearish",
    "falling_wedge": "bullish",
    "bullish_rectangle": "bullish",
    "bearish_rectangle": "bearish",
    "bull_flag": "bullish",
    "bear_flag": "bearish",
    "head_shoulders": "bearish",
    "inverse_head_shoulders": "bullish",
    "double_top": "bearish",
    "double_bottom": "bullish",
    "triple_bottom": "bullish",
    "cup_handle": "bullish",
}

_PIVOT_SPAN_MIN_BARS = 15     # a "pattern" narrower than this is noise
_MAX_PIVOTS_PER_LINE = 5
_MIN_LINE_R2 = 0.80           # trendline fit quality gate when >= 3 touches


def _atr(frame: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Same TR/rolling-mean math as analysis.ta_lib.TechnicalIndicators.atr,
    kept separate ON PURPOSE for two behavioral deltas this module needs:
    min_periods=1 (patterns must be scorable near the frame head, where
    ta_lib returns NaN warmup) and prev_close seeded from close[0] instead
    of a NaN shift. If you change ATR semantics in ta_lib, decide explicitly
    whether this copy should follow."""
    high, low, close = frame["high"].values, frame["low"].values, frame["close"].values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return pd.Series(tr).rolling(period, min_periods=1).mean().values


def find_confirmed_pivots(
    high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]], np.ndarray, np.ndarray]:
    """Pivot highs/lows as (index, price), plus per-pivot CONFIRMATION bar
    arrays (index + right) - the first bar at which that pivot is knowable."""
    n = len(high)
    pivot_highs: List[Tuple[int, float]] = []
    pivot_lows: List[Tuple[int, float]] = []
    for i in range(left, n - right):
        window_h = high[i - left: i + right + 1]
        window_l = low[i - left: i + right + 1]
        # The non-degenerate check (max > min) rejects flat plateaus: a dead
        # stretch where every bar "ties for highest" is not a swing point,
        # and without this a flat tail floods the pivot list with junk that
        # displaces the real pattern peaks from the last-N-pivots windows.
        if high[i] >= window_h.max() and window_h.max() > window_h.min():
            pivot_highs.append((i, float(high[i])))
        if low[i] <= window_l.min() and window_l.max() > window_l.min():
            pivot_lows.append((i, float(low[i])))

    def _dedupe(pivots: List[Tuple[int, float]], keep_max: bool) -> List[Tuple[int, float]]:
        # A flat swing (plateau) satisfies the pivot test on several
        # adjacent bars - that's ONE swing point, not several. Collapsing
        # them matters downstream: double-top spacing checks compare the
        # last two pivots, and a duplicated plateau makes them 1 bar apart.
        deduped: List[Tuple[int, float]] = []
        for idx, price in pivots:
            if deduped and idx - deduped[-1][0] <= right:
                if (keep_max and price > deduped[-1][1]) or (not keep_max and price < deduped[-1][1]):
                    deduped[-1] = (idx, price)
                continue
            deduped.append((idx, price))
        return deduped

    pivot_highs = _dedupe(pivot_highs, keep_max=True)
    pivot_lows = _dedupe(pivot_lows, keep_max=False)
    high_confirm = np.array([i + right for i, _ in pivot_highs], dtype=int)
    low_confirm = np.array([i + right for i, _ in pivot_lows], dtype=int)
    return pivot_highs, pivot_lows, high_confirm, low_confirm


def _fit_line(points: List[Tuple[int, float]]) -> Optional[Dict[str, float]]:
    if len(points) < 2:
        return None
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - ys.mean()) ** 2))
    r2 = 1.0 if ss_tot < 1e-12 else max(0.0, 1.0 - ss_res / ss_tot)
    if len(points) >= 3 and r2 < _MIN_LINE_R2:
        return None
    return {"slope": float(slope), "intercept": float(intercept), "r2": r2,
            "x0": float(xs[0]), "x1": float(xs[-1]), "n": len(points)}


def _best_line(points: List[Tuple[int, float]]) -> Optional[Dict[str, float]]:
    """Fit the longest RECENT run of pivots that forms a clean line: try the
    last 5, then 4, then 3, then 2. Without this, one stale pre-pattern
    pivot inside the window poisons the fit, the R2 gate rejects it, and a
    textbook pattern whose own touches are perfectly collinear goes
    undetected."""
    for k in range(min(_MAX_PIVOTS_PER_LINE, len(points)), 1, -1):
        fitted = _fit_line(points[-k:])
        if fitted is not None:
            return fitted
    return None


def _classify_lines(
    top: Optional[Dict[str, float]],
    bot: Optional[Dict[str, float]],
    closes: np.ndarray,
    i: int,
    atr_i: float,
) -> Dict[str, float]:
    """Triangles / wedges / rectangles / flags from the two fitted lines."""
    out: Dict[str, float] = {}
    if top is None or bot is None or atr_i <= 0:
        return out
    span = max(top["x1"], bot["x1"]) - min(top["x0"], bot["x0"])
    if span < _PIVOT_SPAN_MIN_BARS:
        return out

    # Slopes in ATR-per-bar units: comparable across symbols and timeframes.
    # Calibrated against textbook consolidation geometry: inside a pattern
    # the range is compressed by definition, so a trendline moving ~0.1 ATR
    # per bar is already decisively directional; 0.25 was strict enough to
    # reject clean textbook triangles outright.
    st, sb = top["slope"] / atr_i, bot["slope"] / atr_i
    flat_t, flat_b = abs(st) < 0.05, abs(sb) < 0.05
    up_t, up_b = st > 0.08, sb > 0.08
    down_t, down_b = st < -0.08, sb < -0.08

    x_start, x_end = min(top["x0"], bot["x0"]), float(i)
    width = lambda x: (top["slope"] * x + top["intercept"]) - (bot["slope"] * x + bot["intercept"])  # noqa: E731
    w_start, w_end = width(x_start), width(x_end)
    converging = w_start > 0 and w_end > 0 and w_start > w_end * 1.25
    parallel_flat = flat_t and flat_b and w_start > 0 and abs(w_start - w_end) < 0.35 * max(w_start, w_end)

    base_conf = (top["r2"] + bot["r2"]) / 2
    if top["n"] < 3 or bot["n"] < 3:
        base_conf *= 0.8

    if flat_t and up_b and converging:
        out["ascending_triangle"] = base_conf
    if flat_b and down_t and converging:
        out["descending_triangle"] = base_conf
    if down_t and up_b and converging:
        out["symmetric_triangle"] = base_conf
    if up_t and up_b and converging and sb > st:
        out["rising_wedge"] = base_conf
    if down_t and down_b and converging and st < sb:
        out["falling_wedge"] = base_conf

    if parallel_flat:
        # Rectangles are continuation patterns: the trend INTO the range
        # decides bullish vs bearish.
        lookback = int(x_start)
        pre = closes[max(0, lookback - 20): lookback + 1]
        if len(pre) >= 5:
            drift = (pre[-1] - pre[0]) / atr_i
            if drift > 2.0:
                out["bullish_rectangle"] = base_conf
            elif drift < -2.0:
                out["bearish_rectangle"] = base_conf

    return out


def _flags(closes: np.ndarray, i: int, atr_i: float) -> Dict[str, float]:
    """Flag = sharp pole then a brief, shallow counter-trend drift."""
    out: Dict[str, float] = {}
    if i < 20 or atr_i <= 0:
        return out
    pole = (closes[i - 8] - closes[i - 18]) / atr_i
    drift_seg = closes[i - 8: i + 1]
    drift = (drift_seg[-1] - drift_seg[0]) / atr_i
    depth = (drift_seg.max() - drift_seg.min()) / atr_i
    if depth > 2.5:
        return out
    conf = min(1.0, abs(pole) / 6.0)
    if pole > 3.5 and -2.0 < drift < -0.1:
        out["bull_flag"] = conf
    elif pole < -3.5 and 0.1 < drift < 2.0:
        out["bear_flag"] = conf
    return out


def _tops_bottoms(
    highs: List[Tuple[int, float]],
    lows: List[Tuple[int, float]],
    closes: np.ndarray,
    i: int,
    atr_i: float,
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if atr_i <= 0:
        return out

    def _equal(a: float, b: float, tol: float) -> bool:
        return abs(a - b) <= tol * atr_i

    # Tolerances tuned against a random-walk false-positive benchmark (see
    # module tests): at 0.6 ATR equality / 1.2 ATR depth these fired on
    # ~12% of pure-noise bars, which would drown the real signal.
    if len(highs) >= 2:
        (i1, h1), (i2, h2) = highs[-2], highs[-1]
        if i2 - i1 >= 10 and _equal(h1, h2, 0.35):
            trough = closes[i1:i2 + 1].min()
            if min(h1, h2) - trough >= 1.5 * atr_i:
                out["double_top"] = 1.0 - abs(h1 - h2) / (0.35 * atr_i) * 0.4

    if len(lows) >= 2:
        (i1, l1), (i2, l2) = lows[-2], lows[-1]
        if i2 - i1 >= 10 and _equal(l1, l2, 0.35):
            peak = closes[i1:i2 + 1].max()
            if peak - max(l1, l2) >= 1.5 * atr_i:
                out["double_bottom"] = 1.0 - abs(l1 - l2) / (0.35 * atr_i) * 0.4

    if len(lows) >= 3:
        (ia, la), (ib, lb), (ic, lc) = lows[-3], lows[-2], lows[-1]
        if ic - ia >= 20 and _equal(la, lb, 0.35) and _equal(lb, lc, 0.35) and _equal(la, lc, 0.35):
            peak = closes[ia:ic + 1].max()
            if peak - max(la, lb, lc) >= 1.5 * atr_i:
                spread = (max(la, lb, lc) - min(la, lb, lc)) / (0.35 * atr_i)
                out["triple_bottom"] = 1.0 - spread * 0.4
                out.pop("double_bottom", None)  # the stronger reading wins

    if len(highs) >= 3:
        (il, hl), (ih, hh), (ir, hr) = highs[-3], highs[-2], highs[-1]
        if ir - il >= 15 and hh > hl + 1.0 * atr_i and hh > hr + 1.0 * atr_i and _equal(hl, hr, 0.5):
            out["head_shoulders"] = 1.0 - abs(hl - hr) / (0.5 * atr_i) * 0.4

    if len(lows) >= 3:
        (il, ll), (ih, lh), (ir, lr) = lows[-3], lows[-2], lows[-1]
        if ir - il >= 15 and lh < ll - 1.0 * atr_i and lh < lr - 1.0 * atr_i and _equal(ll, lr, 0.5):
            out["inverse_head_shoulders"] = 1.0 - abs(ll - lr) / (0.5 * atr_i) * 0.4

    return {k: float(np.clip(v, 0.1, 1.0)) for k, v in out.items()}


def _cup_handle(lows: np.ndarray, closes: np.ndarray, i: int, atr_i: float) -> Dict[str, float]:
    """Rounded (quadratic, opening upward) base ending ~8 bars back, then a
    shallow handle pullback into the current bar."""
    cup_len, handle_len = 45, 8
    if i < cup_len + handle_len or atr_i <= 0:
        return {}
    seg = lows[i - cup_len - handle_len: i - handle_len + 1]
    xs = np.arange(len(seg), dtype=float)
    a, b, c = np.polyfit(xs, seg, 2)
    if a <= 0:
        return {}
    pred = a * xs * xs + b * xs + c
    ss_res = float(np.sum((seg - pred) ** 2))
    ss_tot = float(np.sum((seg - seg.mean()) ** 2))
    r2 = 1.0 if ss_tot < 1e-12 else 1.0 - ss_res / ss_tot
    vertex_x = -b / (2 * a)
    if not (len(seg) * 0.25 <= vertex_x <= len(seg) * 0.75):
        return {}
    depth = (max(seg[0], seg[-1]) - seg.min())
    if depth < 2.0 * atr_i or abs(seg[0] - seg[-1]) > 1.2 * atr_i:
        return {}
    handle = closes[i - handle_len: i + 1]
    handle_drop = handle.max() - handle.min()
    if handle_drop > 0.5 * depth or handle.min() < seg.min() + 0.5 * depth:
        return {}
    return {"cup_handle": float(np.clip(r2, 0.1, 1.0))}


def _detect_at(
    i: int,
    highs: List[Tuple[int, float]],
    lows: List[Tuple[int, float]],
    closes: np.ndarray,
    lows_arr: np.ndarray,
    atr: np.ndarray,
    window: int,
) -> Dict[str, float]:
    """All patterns readable at bar i from already-confirmed pivots."""
    cutoff = i - window
    hs = [p for p in highs if p[0] >= cutoff][-_MAX_PIVOTS_PER_LINE:]
    ls = [p for p in lows if p[0] >= cutoff][-_MAX_PIVOTS_PER_LINE:]
    # ATR averaged over the pattern window, not at its right edge: inside a
    # converging pattern the range compresses by definition, so end-of-
    # pattern ATR understates the pattern's own scale and pushes its
    # trendline slopes into the "flat" dead zone.
    atr_i = float(atr[max(0, cutoff):i + 1].mean())

    found: Dict[str, float] = {}
    found.update(_classify_lines(_best_line(hs), _best_line(ls), closes, i, atr_i))
    found.update(_flags(closes, i, atr_i))
    found.update(_tops_bottoms(hs, ls, closes, i, atr_i))
    found.update(_cup_handle(lows_arr, closes, i, atr_i))
    return found


def pattern_feature_frame(
    frame: pd.DataFrame, left: int = 3, right: int = 3, window: int = 60
) -> pd.DataFrame:
    """One row per input bar, one column per PATTERN_COLUMNS entry, value =
    confidence of that pattern being present at that bar (0.0 if absent).
    Uses only pivots confirmed by each bar - safe to join onto training
    labels without leaking the future."""
    high, low, close = frame["high"].values, frame["low"].values, frame["close"].values
    n = len(frame)
    atr = _atr(frame)
    pivot_highs, pivot_lows, high_conf, low_conf = find_confirmed_pivots(high, low, left, right)

    out = np.zeros((n, len(PATTERN_NAMES)), dtype=float)
    col_idx = {name: j for j, name in enumerate(PATTERN_NAMES)}

    hi_ptr = lo_ptr = 0
    visible_highs: List[Tuple[int, float]] = []
    visible_lows: List[Tuple[int, float]] = []
    for i in range(n):
        while hi_ptr < len(pivot_highs) and high_conf[hi_ptr] <= i:
            visible_highs.append(pivot_highs[hi_ptr]); hi_ptr += 1
        while lo_ptr < len(pivot_lows) and low_conf[lo_ptr] <= i:
            visible_lows.append(pivot_lows[lo_ptr]); lo_ptr += 1
        if i < _PIVOT_SPAN_MIN_BARS:
            continue
        for name, conf in _detect_at(i, visible_highs, visible_lows, close, low, atr, window).items():
            out[i, col_idx[name]] = conf

    return pd.DataFrame(out, columns=PATTERN_COLUMNS, index=frame.index)


def detect_patterns(
    frame: pd.DataFrame, left: int = 3, right: int = 3, window: int = 60, extend_bars: int = 5
) -> List[Dict[str, Any]]:
    """Patterns present at the last bar, with drawable line segments.
    Timestamps are epoch SECONDS (frame['timestamp'] may be datetimes or ms
    epochs). Trendlines are extended `extend_bars` beyond the last bar -
    the forward projection of the pattern's own geometry."""
    if len(frame) < _PIVOT_SPAN_MIN_BARS + right + 1:
        return []
    high, low, close = frame["high"].values, frame["low"].values, frame["close"].values
    atr = _atr(frame)
    i = len(frame) - 1

    ts = frame["timestamp"]
    if np.issubdtype(ts.dtype, np.number):
        times = (ts.values / 1000).astype(np.int64)  # ms -> s
    else:
        times = (pd.to_datetime(ts).astype(np.int64) // 10**9).values
    bar_seconds = int(np.median(np.diff(times))) if len(times) > 1 else 60

    def t_of(x: float) -> int:
        # Linear bar-index -> epoch-seconds, valid beyond the last bar too.
        return int(times[0] + x * bar_seconds)

    pivot_highs, pivot_lows, high_conf, low_conf = find_confirmed_pivots(high, low, left, right)
    visible_highs = [p for k, p in enumerate(pivot_highs) if high_conf[k] <= i]
    visible_lows = [p for k, p in enumerate(pivot_lows) if low_conf[k] <= i]

    found = _detect_at(i, visible_highs, visible_lows, close, low, atr, window)

    cutoff = i - window
    hs = [p for p in visible_highs if p[0] >= cutoff][-_MAX_PIVOTS_PER_LINE:]
    ls = [p for p in visible_lows if p[0] >= cutoff][-_MAX_PIVOTS_PER_LINE:]
    top, bot = _best_line(hs), _best_line(ls)

    def _line_points(line: Dict[str, float], role: str) -> Dict[str, Any]:
        x_end = i + extend_bars
        return {
            "role": role,
            "t0": t_of(line["x0"]), "p0": line["slope"] * line["x0"] + line["intercept"],
            "t1": t_of(x_end), "p1": line["slope"] * x_end + line["intercept"],
        }

    results: List[Dict[str, Any]] = []
    line_patterns = {
        "ascending_triangle", "descending_triangle", "symmetric_triangle",
        "rising_wedge", "falling_wedge", "bullish_rectangle", "bearish_rectangle",
    }
    for name, conf in sorted(found.items(), key=lambda kv: -kv[1]):
        entry: Dict[str, Any] = {
            "name": name,
            "bias": PATTERN_BIAS[name],
            "confidence": round(float(conf), 3),
            "lines": [],
        }
        if name in line_patterns and top is not None and bot is not None:
            entry["lines"] = [_line_points(top, "resistance"), _line_points(bot, "support")]
        elif name in ("double_top", "head_shoulders") and len(hs) >= 2:
            level = float(np.mean([p[1] for p in hs[-2:]]))
            entry["lines"] = [{"role": "level", "t0": t_of(hs[-2][0]), "p0": level, "t1": t_of(i + extend_bars), "p1": level}]
        elif name in ("double_bottom", "triple_bottom", "inverse_head_shoulders") and len(ls) >= 2:
            level = float(np.mean([p[1] for p in ls[-2:]]))
            entry["lines"] = [{"role": "level", "t0": t_of(ls[-2][0]), "p0": level, "t1": t_of(i + extend_bars), "p1": level}]
        results.append(entry)
    return results
