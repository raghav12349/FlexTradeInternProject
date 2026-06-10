"""Shared rating scale: turn a normalized score into a label."""
from __future__ import annotations

RATINGS = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]


def score_to_rating(score: float) -> str:
    """Map a score in [-1, 1] to one of the 5 labels.

    Thresholds: >=0.6 Strong Buy, >=0.2 Buy, >-0.2 Hold, >-0.6 Sell, else Strong Sell.
    """
    if score >= 0.6:
        return "Strong Buy"
    if score >= 0.2:
        return "Buy"
    if score > -0.2:
        return "Hold"
    if score > -0.6:
        return "Sell"
    return "Strong Sell"


def clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))
