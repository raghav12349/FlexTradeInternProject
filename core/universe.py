"""Resolve a user's input into a list of tickers.

Accepts either:
  * a free-text list of tickers  ("AAPL NVDA, TSLA")
  * the name of a predefined universe / sector ("MEGACAP", "TECH")

The named universes below are PLACEHOLDERS — short hand-picked baskets so the
recommender has something to rank today. Replace them (or add an index/sector
lookup) with Massive reference-data endpoints once that's wired up, e.g. pull
real index constituents instead of these static lists.
"""
from __future__ import annotations

import re

UNIVERSES: dict[str, list[str]] = {
    "MEGACAP": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"],
    "TECH": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD"],
    "ENERGY": ["XOM", "CVX", "COP", "SLB", "EOG", "PSX"],
    "FINANCE": ["JPM", "BAC", "WFC", "GS", "MS", "C"],
    "HEALTHCARE": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV"],
}


def available() -> list[str]:
    """Names of the predefined universes (for a dropdown)."""
    return sorted(UNIVERSES)


def resolve(query: str) -> list[str]:
    """Turn `query` into an ordered, de-duplicated list of ticker symbols."""
    q = (query or "").strip()
    if not q:
        return []
    if q.upper() in UNIVERSES:
        tickers = UNIVERSES[q.upper()]
    else:
        tickers = [t.upper() for t in re.split(r"[,\s]+", q) if t]

    seen: set[str] = set()
    ordered: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered
