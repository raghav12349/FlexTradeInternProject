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
        "native_score": f"{raw:g}/10",       # aarav's own 1-10 scale
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
        "native_score": str(raw),            # anshu's own integer score
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
        "native_score": f"{comp:.2f}",       # diya's own 0-1 composite
        "native_rating": res.get("signal"),
        "breakdown": lines,
        "details": {"signal": res.get("signal"), "composite_score": comp,
                    "operational_score": op, "financial_score": fin,
                    "n_periods": res.get("n_periods")},
    }


def _aarav2(mod: ModuleType, ticker: str, period: str) -> dict:
    """aarav's second indicator: RSI. Native 1-10 composite -> [-1, 1]."""
    tf = _AARAV_TF.get(period.lower(), "ALL")  # context window; RSI uses full series
    end = date.today()
    start = end - timedelta(days=730)

    try:
        details = mod.fetch_ticker_details(ticker)
    except Exception:  # noqa: BLE001
        details = None
    prices, volumes = mod.fetch_daily_prices(ticker, start.isoformat(), end.isoformat())
    rsi_series = mod.fetch_rsi_series(ticker)
    res = mod.analyze(ticker, prices, volumes, details, rsi_series)

    comp = res.get("composite")
    if not comp or comp.get("score") is None:
        raise ValueError(f"no RSI data for {ticker}")
    raw = comp["score"]                       # 1..10
    score = clip(round((raw - 5.5) / 4.5, 3))

    rsi = res.get("rsi") or {}
    ctx = res.get("context") or {}
    cap = res.get("cap") or {}
    lines = [
        f"{res.get('company', ticker)} — {cap.get('tier', '?')}, price ${ctx.get('price', '?')}",
        f"RSI {rsi.get('current', '?')} ({rsi.get('label', '?')}), "
        f"regime {rsi.get('regime', '?')} {rsi.get('regime_days', '?')}d, "
        f"percentile {rsi.get('percentile', '?')}",
        f"Composite {raw}/10 -> {comp.get('recommendation')} ({comp.get('trend')})",
    ]
    return {
        "ticker": ticker.upper(),
        "signal": "rsi",
        "score": score,
        "rating": score_to_rating(score),
        "native_score": f"{raw:g}/10",
        "native_rating": comp.get("recommendation"),
        "breakdown": lines,
        "details": {"raw_score_1_10": raw, "rsi": rsi.get("current"),
                    "label": rsi.get("label"), "regime": rsi.get("regime")},
    }


# samar scores against a fixed ~110-stock universe; that's the same for every
# ticker, so compute it once per process and reuse.
_SAMAR_UNIVERSE: dict = {}


def _samar_universe(mod: ModuleType):
    if "data" not in _SAMAR_UNIVERSE:
        snap = mod.get_market_snapshots()
        universe = list(mod.DIVERSE_UNIVERSE.keys())
        momentum_dict, _ = mod.compute_momentum(universe, snap)
        cap_tiers = mod.build_cap_tiers()
        results, stats = mod.standardize(momentum_dict, mod.DIVERSE_UNIVERSE, cap_tiers)
        _SAMAR_UNIVERSE["data"] = (snap, momentum_dict, results, stats, cap_tiers)
    return _SAMAR_UNIVERSE["data"]


def _samar(mod: ModuleType, ticker: str, period: str) -> dict:
    """samar's cross-sectional 3/6/12 momentum, scored vs his diverse universe
    (replicates run_vs_diverse without the printing). Native 1-10 -> [-1, 1]."""
    ticker = ticker.upper().strip()
    snap, momentum_dict, results, stats, cap_tiers = _samar_universe(mod)

    if ticker in results:
        v = results[ticker]
    else:
        extra, _ = mod.compute_momentum([ticker], snap)
        if ticker not in extra:
            raise ValueError(f"no valid momentum data for {ticker}")
        v = mod._score_extra_ticker(ticker, extra[ticker], momentum_dict, results,
                                    stats, mod.DIVERSE_UNIVERSE, cap_tiers)

    raw = v["score_1_10"]                      # 1..10
    score = clip(round((raw - 5.5) / 4.5, 3))
    sec = v.get("z_combined_sector")
    lines = [
        f"12-1 mom {v['m12_pct']:+.2f}% (z {v['z12']:+.2f})",
        f"6-1  mom {v['m6_pct']:+.2f}% (z {v['z6']:+.2f})",
        f"3-1  mom {v['m3_pct']:+.2f}% (z {v['z3']:+.2f})",
        f"Combined z {v['z_combined']:+.2f}  strong={v['is_strong']}  shape={v['shape']}"
        + (f"  sector-rel z {sec:+.2f}" if sec is not None else ""),
        f"Score {raw:.1f}/10 -> {v['recommendation']}",
    ]
    return {
        "ticker": ticker.upper(),
        "signal": "momentum",
        "score": score,
        "rating": score_to_rating(score),
        "native_score": f"{raw:.1f}/10",
        "native_rating": v["recommendation"],   # STRONG BUY / BUY / HOLD / DON'T BUY
        "breakdown": lines,
        "details": {"score_1_10": raw, "z_combined": v["z_combined"],
                    "is_strong": v["is_strong"], "shape": v["shape"]},
    }


def _justin(mod: ModuleType, ticker: str, period: str) -> dict:
    """justin's sector-aware financial-ratios rater. Native 1-10 -> [-1, 1].
    He outputs a numeric score (no buy/sell label), so the native rating IS the
    score out of 10."""
    res = mod.rate_stock(ticker)
    raw = res.get("score")
    if raw is None:
        raise ValueError(f"no ratios for {ticker}")
    score = clip(round((raw - 5.5) / 4.5, 3))
    lines = [f"Sector: {res.get('sector', '?')}  —  score {raw:.1f}/10"]
    for metric, info in (res.get("breakdown") or {}).items():
        lines.append(f"{metric}: {info.get('value'):.3f} -> {info.get('score'):.1f}/10")
    return {
        "ticker": ticker.upper(),
        "signal": "ratios",
        "score": score,
        "rating": score_to_rating(score),
        "native_score": f"{raw:.1f}/10",
        "native_rating": f"{raw:.1f}/10",
        "breakdown": lines,
        "details": {"score_1_10": raw, "sector": res.get("sector")},
    }


def _anshu2(mod: ModuleType, ticker: str, period: str) -> dict:
    """anshu's second signal: short interest / squeeze. Native integer -> [-1, 1]."""
    earliest = date(2015, 1, 1)
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 365))
    if start < earliest:
        start = earliest

    res = mod.rate_ticker(ticker, start.isoformat(), end.isoformat())
    sig = res.get("signal")
    if sig in ("N/A", "NO DATA", "ERROR"):
        raise ValueError("; ".join(res.get("reasoning") or []) or str(sig))

    raw = res.get("score", 0)
    score = clip(round(raw / 10.0, 3))
    head = f"Signal {sig}  (score {raw})"
    if res.get("short_pct_float") is not None:
        head += f"  short {res['short_pct_float']}% of float"
    if res.get("days_to_cover") is not None:
        head += f"  days-to-cover {res['days_to_cover']}"
    if res.get("squeeze_alert"):
        head += "  ⚡ squeeze alert"
    lines = [head, *(res.get("reasoning") or [])]
    return {
        "ticker": ticker.upper(),
        "signal": "short_interest",
        "score": score,
        "rating": score_to_rating(score),
        "native_score": str(raw),
        "native_rating": sig,
        "breakdown": lines,
        "details": {"raw_score": raw, "signal": sig,
                    "short_pct_float": res.get("short_pct_float"),
                    "days_to_cover": res.get("days_to_cover"),
                    "squeeze_alert": res.get("squeeze_alert")},
    }


# cosmo's insider signal is categorical (BULLISH/BEARISH/NEUTRAL); map to a
# coarse score so it contributes to the composite.
_COSMO_SCORE = {"BULLISH": 0.5, "BEARISH": -0.5, "NEUTRAL": 0.0}


def _cosmo(mod: ModuleType, ticker: str, period: str) -> dict:
    """cosmo's Form-4 insider-transaction signal. Categorical -> coarse score."""
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 180))
    txns = mod.fetch_transactions(ticker, start.isoformat(), end.isoformat(), 200)
    res = mod.compute_signal(txns)
    sig = res.get("signal", "NEUTRAL")
    score = _COSMO_SCORE.get(sig, 0.0)
    return {
        "ticker": ticker.upper(),
        "signal": "insider",
        "score": score,
        "rating": score_to_rating(score),
        "native_score": "—",                 # categorical, no number
        "native_rating": sig,
        "breakdown": [res.get("reason", "")],
        "details": {"signal": sig, "reason": res.get("reason")},
    }


ADAPTERS: dict[str, dict] = {
    "samar": {"name": "momentum", "owner": "samar", "category": "Technicals", "analyze": _samar},
    "aarav": {"name": "macd", "owner": "aarav", "category": "Technicals", "analyze": _aarav},
    "aarav2": {"name": "rsi", "owner": "aarav2", "category": "Technicals", "analyze": _aarav2},
    "justin": {"name": "ratios", "owner": "justin", "category": "Fundamentals", "analyze": _justin},
    "anshu": {"name": "dividends", "owner": "anshu", "category": "Fundamentals", "analyze": _anshu},
    "diya": {"name": "liquidity", "owner": "diya", "category": "Fundamentals", "analyze": _diya},
    "anshu2": {"name": "short_interest", "owner": "anshu2", "category": "Sentiment", "analyze": _anshu2},
    "cosmo": {"name": "insider", "owner": "cosmo", "category": "Sentiment", "analyze": _cosmo},
}
