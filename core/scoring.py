"""Shared 1-10 scoring scale.

Every numeric signal is shown on a common 1-10 scale so they're comparable and
can be averaged into a composite. Some authors already score on 1-10 (aarav,
samar, justin); others use their own range (anshu -15..21, diya 0-1) and we
convert. Qualitative signals (e.g. cosmo's BULLISH/BEARISH) carry no number and
are shown as labels — never forced onto the scale.
"""
from __future__ import annotations

# Native numeric range per signal, used to map onto 1-10. Signals already on
# 1-10 don't need an entry. Qualitative signals are not listed (ten = None).
NATIVE_RANGES: dict[str, tuple[float, float]] = {
    "dividends": (-15.0, 21.0),       # anshu
    "short_interest": (-8.0, 12.0),   # anshu2
    "liquidity": (0.0, 1.0),          # diya
}


def to_ten(value: float | None, lo: float, hi: float) -> float | None:
    """Map a value in [lo, hi] onto [1, 10] (clamped, one decimal)."""
    if value is None or hi == lo:
        return None
    t = (value - lo) / (hi - lo) * 9.0 + 1.0
    return round(max(1.0, min(10.0, t)), 1)


def ten_to_label(ten: float | None) -> str:
    """House label for a 1-10 score (used where an author has no own label)."""
    if ten is None:
        return "N/A"
    if ten >= 8.0:
        return "Strong Buy"
    if ten >= 6.5:
        return "Buy"
    if ten >= 4.5:
        return "Hold"
    if ten >= 3.0:
        return "Sell"
    return "Strong Sell"


def fmt_ten(ten: float | None) -> str:
    return f"{ten:.1f}/10" if isinstance(ten, (int, float)) else "—"
