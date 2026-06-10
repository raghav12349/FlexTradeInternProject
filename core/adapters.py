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

from core.massive import period_to_days
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


def _anshu(mod: ModuleType, ticker: str, period: str) -> dict:
    """Run anshu's dividend-growth/payout module and map its score onto [-1, 1].

    rate_ticker(ticker, start, end) returns an integer score (~ -11..+18) plus a
    signal string. Dividend analysis needs a long window, so we floor the range
    at 2 years and clamp to anshu's earliest supported date (2015-01-01).
    """
    earliest = date(2015, 1, 1)
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 730))
    if start < earliest:
        start = earliest

    res = mod.rate_ticker(ticker, start.isoformat(), end.isoformat())
    sig = res.get("signal")
    if sig in ("N/A", "ERROR"):
        raise ValueError("; ".join(res.get("reasoning") or []) or str(sig))

    raw = res.get("score", 0)
    score = clip(round(raw / 16.0, 3))  # ~+16 (Strong Buy) -> +1
    return {
        "ticker": ticker.upper(),
        "signal": "dividends",
        "score": score,
        "rating": score_to_rating(score),
        "details": {
            "raw_score": raw,
            "signal": sig,
            "payout_ratio": res.get("payout_ratio"),
            "market_cap": res.get("market_cap"),
        },
    }


def _diya(mod: ModuleType, ticker: str, period: str) -> dict:
    """Run diya's liquidity module and map its 0-1 composite onto [-1, 1].

    liquidity_analysis(ticker, start, end) returns a 0-1 composite_score plus a
    four-band signal (or "NO_DATA"). Fundamentals are annual, so we floor the
    window at ~3 years. save_detail=False so it doesn't write JSON files.
    """
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 1095))
    res = mod.liquidity_analysis(ticker, start.isoformat(), end.isoformat(),
                                 save_detail=False)
    if res.get("signal") == "NO_DATA" or res.get("composite_score") is None:
        raise ValueError("no liquidity data (Massive Advanced tier required)")

    comp = res["composite_score"]                 # 0..1
    score = clip(round(comp * 2 - 1, 3))          # -> [-1, 1]
    return {
        "ticker": ticker.upper(),
        "signal": "liquidity",
        "score": score,
        "rating": score_to_rating(score),
        "details": {
            "signal": res.get("signal"),
            "composite_score": comp,
            "operational_score": res.get("operational_score"),
            "financial_score": res.get("financial_score"),
            "n_periods": res.get("n_periods"),
        },
    }


ADAPTERS: dict[str, dict] = {
    "aarav": {
        "name": "macd",
        "owner": "aarav",
        "category": "Technicals",
        "analyze": _aarav,
    },
    "anshu": {
        "name": "dividends",
        "owner": "anshu",
        "category": "Fundamentals",
        "analyze": _anshu,
    },
    "diya": {
        "name": "liquidity",
        "owner": "diya",
        "category": "Fundamentals",
        "analyze": _diya,
    },
}
