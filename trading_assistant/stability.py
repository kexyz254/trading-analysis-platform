"""Lightweight strategy-stability calculations shared by engine and UI."""

from __future__ import annotations

from typing import List, Optional


def compute_stability_score(history: List[Optional[str]]) -> Optional[float]:
    """Return the share of consecutive tournament runs with the same winner."""
    if len(history) < 2:
        return None
    matches = sum(
        1 for index in range(1, len(history))
        if history[index] == history[index - 1]
    )
    return matches / (len(history) - 1)
