"""Cosmo — TBD signal.

PLACEHOLDER: `analyze` returns an arbitrary score so the bot runs end-to-end.
Pick a signal, then replace the body below. Fetch data however you like —
`core.massive.aggregates(...)` for OHLCV, `core.massive.get(path, params)` for
any other endpoint, or `requests` directly. Normalize your score to [-1, 1]
and keep the return shape identical so the rest of the pipeline keeps working.
"""
from __future__ import annotations

import random

from core.rating import score_to_rating

SIGNAL_NAME = "cosmo_signal"
SIGNAL_OWNER = "cosmo"
SIGNAL_CATEGORY = "Undecided"  # set to "Fundamentals" or "Technicals" when picked


def analyze(ticker: str, period: str = "2y", **_) -> dict:
    # PLACEHOLDER — stable arbitrary value per ticker until real logic lands.
    rng = random.Random(f"{ticker.upper()}:{SIGNAL_NAME}")
    score = round(rng.uniform(-1.0, 1.0), 3)

    return {
        "ticker": ticker.upper(),
        "signal": SIGNAL_NAME,
        "score": score,
        "rating": score_to_rating(score),
        "details": {"note": "placeholder — not yet implemented"},
    }
