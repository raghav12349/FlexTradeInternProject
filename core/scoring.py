"""Shared 1-10 scoring scale.

Every numeric signal is shown on a common 1-10 scale so they're comparable and
can be averaged into a composite. Some authors already score on 1-10 (aarav,
samar, justin); others use their own range (anshu -15..21, diya 0-1) and we
convert. Qualitative insider labels (cosmo's BULLISH/BEARISH/NEUTRAL) map to
fixed points on the same scale so they participate in comparisons and composite.
"""
from __future__ import annotations

import math

# Native numeric range per signal, used to map onto 1-10. Signals already on
# 1-10 don't need an entry.
NATIVE_RANGES: dict[str, tuple[float, float]] = {
    "dividends": (-15.0, 21.0),       # anshu
    "short_interest": (-8.0, 12.0),   # anshu2
    "liquidity": (0.0, 1.0),          # diya
}

# cosmo insider sentiment → comparable 1-10 anchor (included in composite).
INSIDER_SIGNAL_TEN: dict[str, float] = {
    "BULLISH": 8.0,
    "BEARISH": 2.0,
    "NEUTRAL": 5.0,
}


def is_scored(x) -> bool:
    """True for a finite numeric score usable in composites and tables."""
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def insider_to_ten(signal: str | None) -> float | None:
    """Map cosmo's BULLISH/BEARISH/NEUTRAL onto the shared 1-10 scale."""
    return INSIDER_SIGNAL_TEN.get((signal or "").upper())


def to_ten(value: float | None, lo: float, hi: float) -> float | None:
    """Map a value in [lo, hi] onto [1, 10] (clamped, one decimal)."""
    if value is None or hi == lo:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    t = (value - lo) / (hi - lo) * 9.0 + 1.0
    return round(max(1.0, min(10.0, t)), 1)


def ten_to_label(ten: float | None) -> str:
    """Canonical house rating for a 1-10 score.

    This is the SINGLE vocabulary shown to users everywhere (per-signal ratings,
    composite, recommender), so every factor reads the same way regardless of
    each author's own wording. Authors' native labels are kept only in the
    per-signal breakdown for reference.
    """
    if ten is None:
        return "N/A"
    if ten >= 8.0:
        return "STRONG BUY"
    if ten >= 6.5:
        return "BUY"
    if ten >= 4.5:
        return "HOLD"
    if ten >= 3.0:
        return "SELL"
    return "STRONG SELL"


def fmt_ten(ten: float | None) -> str:
    return f"{ten:.1f}/10" if is_scored(ten) else "—"
