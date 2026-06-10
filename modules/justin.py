"""Justin — basket of technical indicators (RSI, Bollinger, ADX, etc.).

PLACEHOLDER: `analyze` returns an arbitrary score so the bot runs end-to-end.
Replace the body below with the real technicals logic — fetch bars via
`core.massive.aggregates(...)`, compute the signal, normalize to [-1, 1].
Keep the return shape identical so the rest of the pipeline keeps working.
"""
from __future__ import annotations

import random

from core.rating import score_to_rating

SIGNAL_NAME = "technicals"
SIGNAL_OWNER = "justin"
SIGNAL_CATEGORY = "Technicals"


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
