"""Per-factor weights for the composite index.

These weights come from Aarav's *Ultra Composite Index* scheme (see
`COMPOSITE_INDEX.md`): each factor contributes to the composite in proportion to
its weight rather than equally. Weights are keyed by our signal names
(`SIGNAL_NAME` / adapter name), and the composite renormalises over whichever
signals actually produced a score, so a missing signal never distorts the result
(`Σ wᵢ·tenᵢ / Σ wᵢ`).

Mapping notes vs. COMPOSITE_INDEX.md:
  * The doc's "Short Interest" (8%) cites `aarav6.py`, which is our
    `short_volume` signal — so that 8% lands on `short_volume`.
  * `short_interest` (anshu2) is a separate factor the doc doesn't list; it gets
    DEFAULT_WEIGHT. Adjust below if a specific weight is desired.
"""
from __future__ import annotations

# Listed weights sum to 1.00 (renormalisation makes the absolute scale moot).
COMPOSITE_WEIGHTS: dict[str, float] = {
    "sma":           0.12,   # aarav3 — highest individual weight (structural)
    "ratios":        0.12,   # justin — fundamental quality (tied highest)
    "ema":           0.10,   # aarav4
    "rsi":           0.09,   # aarav2
    "momentum":      0.09,   # samar — cross-sectional momentum
    "macd":          0.08,   # aarav
    "news":          0.08,   # raghav_news
    "short_volume":  0.08,   # aarav6 — doc's "Short Interest"
    "insider":       0.07,   # cosmo
    "liquidity":     0.07,   # diya
    "dividends":     0.05,   # anshu
    "sma_crossover": 0.05,   # kavin — confirmation tier
}

# Weight for any registered signal not listed above (e.g. anshu2's
# `short_interest`, or a teammate's newly added module). Keeps the composite
# robust as modules come and go.
DEFAULT_WEIGHT: float = 0.05


def weight_for(signal_name: str) -> float:
    """Weight for a signal, falling back to DEFAULT_WEIGHT if unlisted."""
    return COMPOSITE_WEIGHTS.get(signal_name, DEFAULT_WEIGHT)


def weight_pct(signal_name: str) -> str:
    """Weight as a short percent string for display, e.g. '12%'."""
    return f"{round(weight_for(signal_name) * 100)}%"
