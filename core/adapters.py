"""Adapters that wrap a teammate's raw module into the standard contract.

Some people write self-contained research scripts that don't follow
`analyze(ticker, period) -> {"score" in [-1, 1], ...}`. Rather than editing
their files (they push often, and each module is meant to be self-contained),
we wrap them here: each adapter supplies the signal metadata AND a function
that runs their code and normalizes the result onto our score scale.

To integrate a new non-conforming module, add an entry to ADAPTERS. A module
that already follows the contract needs no adapter.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import ModuleType

from core.rating import clip, score_to_rating

# our period strings -> aarav's timeframe keys
_AARAV_TF = {"6mo": "6M", "1y": "1Y", "2y": "2Y"}


def _aarav(mod: ModuleType, ticker: str, period: str) -> dict:
    """Run aarav's MACD module and map its 1-10 composite onto [-1, 1]."""
    tf = _AARAV_TF.get(period.lower(), "ALL")
    end = date.today()
    start = end - timedelta(days=730)  # 2y history so MA200 / 52w context is valid

    try:
        details = mod.fetch_ticker_details(ticker)
    except Exception:  # noqa: BLE001 - details are optional, don't fail the signal
        details = None

    prices, volumes = mod.fetch_daily_prices(ticker, start.isoformat(), end.isoformat())
    res = mod.analyze(ticker, prices, volumes, details, tf)

    comp = res.get("composite")
    if not comp:
        raise ValueError(f"not enough data for {ticker}")

    raw = comp["score"]                      # 1..10
    score = clip(round((raw - 5.5) / 4.5, 3))  # -> [-1, 1]
    return {
        "ticker": ticker.upper(),
        "signal": "macd",
        "score": score,
        "rating": score_to_rating(score),
        "details": {
            "raw_score_1_10": raw,
            "trend": comp.get("trend"),
            "recommendation": comp.get("recommendation"),
            "company": res.get("company"),
            "cap_tier": (res.get("cap") or {}).get("tier"),
            "price": (res.get("context") or {}).get("price"),
        },
    }


ADAPTERS: dict[str, dict] = {
    "aarav": {
        "name": "macd",
        "owner": "aarav",
        "category": "Technicals",
        "analyze": _aarav,
    },
}
