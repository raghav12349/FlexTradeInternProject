"""Adapters that wrap a teammate's raw module into the standard contract.

Some people write self-contained research scripts that don't follow
`analyze(ticker, period) -> {"score" in [-1, 1], ...}`. Rather than editing
their files (they push often, and each module is meant to be self-contained),
we wrap them here: each adapter runs their code, normalizes the score onto our
scale FOR THE COMPOSITE, but also carries their OWN rating label and a
human-readable breakdown so the terminal/dashboard can show how their rating
was actually computed — faithfully, in their own logic.

Each adapter returns:
    score          float in [-1, 1]   (for the composite/ranking only)
    rating         our label from that score
    native_rating  their own label (e.g. aarav "Neutral / Hold", anshu "BUY")
    breakdown      list[str], the reasoning behind their rating
    details        structured extras

To integrate a new non-conforming module, add an entry to ADAPTERS.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import ModuleType

from core.massive import period_to_days
from core.rating import clip, score_to_rating

# our period strings -> aarav's timeframe keys
_AARAV_TF = {"6mo": "6M", "1y": "1Y", "2y": "2Y"}


def _aarav(mod: ModuleType, ticker: str, period: str) -> dict:
    """aarav's multi-timeframe MACD. Native 1-10 composite -> [-1, 1]."""
    tf = _AARAV_TF.get(period.lower(), "ALL")
    end = date.today()
    start = end - timedelta(days=730)  # 2y so MA200 / 52w context is valid

    try:
        details = mod.fetch_ticker_details(ticker)
    except Exception:  # noqa: BLE001 - details optional
        details = None

    prices, volumes = mod.fetch_daily_prices(ticker, start.isoformat(), end.isoformat())
    res = mod.analyze(ticker, prices, volumes, details, tf)

    comp = res.get("composite")
    if not comp:
        raise ValueError(f"not enough data for {ticker}")
    raw = comp["score"]                       # 1..10
    score = clip(round((raw - 5.5) / 4.5, 3))

    ctx = res.get("context") or {}
    cap = res.get("cap") or {}
    lines = [
        f"{res.get('company', ticker)} — {cap.get('tier', '?')}, price ${ctx.get('price', '?')}",
        f"200d MA ${ctx.get('ma200', '?')} ({'above' if ctx.get('above_ma200') else 'below'}), "
        f"{ctx.get('pct_from_high', '?')}% below 52w high",
        f"Composite {raw}/10 -> {comp.get('recommendation')} ({comp.get('trend')})",
    ]
    for t in res.get("timeframes", []):
        if "error" in t:
            lines.append(f"{t['label']:<9} {t['error']}")
            continue
        vol = t.get("volume") or {}
        lines.append(
            f"{t['label']:<9} {t['score']}/10  {t['recommendation']:<16} "
            f"MACD {t['macd']:+.4f} hist {t['histogram']:+.4f} vol {vol.get('confidence', '?')}"
        )

    return {
        "ticker": ticker.upper(),
        "signal": "macd",
        "score": score,
        "rating": score_to_rating(score),
        "native_rating": comp.get("recommendation"),
        "breakdown": lines,
        "details": {"raw_score_1_10": raw, "trend": comp.get("trend"),
                    "company": res.get("company"), "cap_tier": cap.get("tier"),
                    "price": ctx.get("price")},
    }


def _anshu(mod: ModuleType, ticker: str, period: str) -> dict:
    """anshu's dividend growth/payout. Native integer score -> [-1, 1]."""
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
    score = clip(round(raw / 16.0, 3))  # ~+16 (his Strong Buy) -> +1

    head = f"Signal {sig}  (score {raw})"
    if res.get("payout_ratio") is not None:
        head += f"  payout {res['payout_ratio']}%"
    if res.get("market_cap"):
        head += f"  cap ${res['market_cap'] / 1e9:.1f}B"
    lines = [head, *(res.get("reasoning") or [])]

    return {
        "ticker": ticker.upper(),
        "signal": "dividends",
        "score": score,
        "rating": score_to_rating(score),
        "native_rating": sig,
        "breakdown": lines,
        "details": {"raw_score": raw, "signal": sig,
                    "payout_ratio": res.get("payout_ratio"),
                    "market_cap": res.get("market_cap")},
    }


def _diya(mod: ModuleType, ticker: str, period: str) -> dict:
    """diya's liquidity factor. Native 0-1 composite -> [-1, 1]."""
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 1095))  # annual data
    res = mod.liquidity_analysis(ticker, start.isoformat(), end.isoformat(),
                                 save_detail=False)
    if res.get("signal") == "NO_DATA" or res.get("composite_score") is None:
        raise ValueError("no liquidity data (Massive Advanced tier required)")

    comp = res["composite_score"]                 # 0..1
    score = clip(round(comp * 2 - 1, 3))
    op, fin = res.get("operational_score"), res.get("financial_score")
    lines = [
        f"Signal {res.get('signal')}  composite {comp:.2f} (0-1 scale)",
        (f"Operational OCF {op:.2f} x0.4  +  Financial FCF {fin:.2f} x0.6"
         if op is not None and fin is not None else "sub-scores unavailable"),
        f"Based on {res.get('n_periods')} annual period(s)",
    ]

    return {
        "ticker": ticker.upper(),
        "signal": "liquidity",
        "score": score,
        "rating": score_to_rating(score),
        "native_rating": res.get("signal"),
        "breakdown": lines,
        "details": {"signal": res.get("signal"), "composite_score": comp,
                    "operational_score": op, "financial_score": fin,
                    "n_periods": res.get("n_periods")},
    }


ADAPTERS: dict[str, dict] = {
    "aarav": {"name": "macd", "owner": "aarav", "category": "Technicals", "analyze": _aarav},
    "anshu": {"name": "dividends", "owner": "anshu", "category": "Fundamentals", "analyze": _anshu},
    "diya": {"name": "liquidity", "owner": "diya", "category": "Fundamentals", "analyze": _diya},
}
