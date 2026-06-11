"""Adapters that wrap a teammate's raw module into the standard contract.

Each teammate writes a self-contained module in their own style. We don't edit
their files (they push often); instead each adapter runs their code and returns:

    ten            float in [1, 10]  (None for purely qualitative signals)
    native_score   display string ("7.1/10", or "—" for qualitative)
    native_rating  the author's OWN label (aarav "Buy", diya "STRONG", ...)
    breakdown      list[str], how their rating was computed
    details        structured extras

`ten` is the common 1-10 scale used for the composite. Authors already on 1-10
pass it through; others are converted from their native range (see core.scoring).
Insider labels (cosmo) map to fixed 1-10 anchors so they count in comparisons.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from types import ModuleType

from core.massive import period_to_days
from core.scoring import fmt_ten, insider_to_ten, is_scored, ten_to_label, to_ten

_AARAV_TF = {"6mo": "6M", "1y": "1Y", "2y": "2Y"}


# ───────────────────────── aarav: MACD / RSI / SMA / EMA ─────────────────────
def _aarav(mod: ModuleType, ticker: str, period: str) -> dict:
    tf = _AARAV_TF.get(period.lower(), "ALL")
    end = date.today()
    start = end - timedelta(days=730)
    try:
        details = mod.fetch_ticker_details(ticker)
    except Exception:  # noqa: BLE001
        details = None
    prices, volumes = mod.fetch_daily_prices(ticker, start.isoformat(), end.isoformat())
    res = mod.analyze(ticker, prices, volumes, details, tf)
    comp = res.get("composite")
    if not comp or comp.get("score") is None:
        raise ValueError(f"not enough data for {ticker}")
    raw = comp["score"]
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
        lines.append(f"{t['label']:<9} {t['score']}/10  {t['recommendation']:<16} "
                     f"MACD {t['macd']:+.4f} hist {t['histogram']:+.4f} vol {vol.get('confidence', '?')}")
    return _result("macd", raw, comp.get("recommendation"), lines,
                   {"raw_score_1_10": raw, "trend": comp.get("trend")})


def _aarav2(mod: ModuleType, ticker: str, period: str) -> dict:
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
    raw = comp["score"]
    rsi = res.get("rsi") or {}
    ctx = res.get("context") or {}
    cap = res.get("cap") or {}
    lines = [
        f"{res.get('company', ticker)} — {cap.get('tier', '?')}, price ${ctx.get('price', '?')}",
        f"RSI {rsi.get('current', '?')} ({rsi.get('label', '?')}), regime {rsi.get('regime', '?')} "
        f"{rsi.get('regime_days', '?')}d, percentile {rsi.get('percentile', '?')}",
        f"Composite {raw}/10 -> {comp.get('recommendation')} ({comp.get('trend')})",
    ]
    return _result("rsi", raw, comp.get("recommendation"), lines,
                   {"raw_score_1_10": raw, "rsi": rsi.get("current")})


def _aarav_ma(mod: ModuleType, ticker: str, signal_name: str,
              windows_attr: str, fetch_attr: str, map_kwarg: str) -> dict:
    """Shared wrapper for aarav's moving-average modules (SMA / EMA)."""
    end = date.today()
    start = end - timedelta(days=730)
    try:
        details = mod.fetch_ticker_details(ticker)
    except Exception:  # noqa: BLE001
        details = None
    prices, volumes = mod.fetch_daily_prices(ticker, start.isoformat(), end.isoformat())
    fetch = getattr(mod, fetch_attr)
    series_map = {w: fetch(ticker, w) for w in getattr(mod, windows_attr)}
    res = mod.analyze(ticker, prices, volumes, details, **{map_kwarg: series_map})
    comp = res.get("composite")
    if not comp or comp.get("score") is None:
        raise ValueError(f"no {signal_name} data for {ticker}")
    raw = comp["score"]
    ctx = res.get("context") or {}
    cap = res.get("cap") or {}
    lines = [
        f"{res.get('company', ticker)} — {cap.get('tier', '?')}, price ${ctx.get('price', '?')}",
        f"200d MA ${ctx.get('ma200', '?')} ({'above' if ctx.get('above_ma200') else 'below'})",
        f"Composite {raw}/10 -> {comp.get('recommendation')} ({comp.get('trend')})",
    ]
    return _result(signal_name, raw, comp.get("recommendation"), lines,
                   {"raw_score_1_10": raw, "trend": comp.get("trend")})


def _aarav3(mod: ModuleType, ticker: str, period: str) -> dict:
    return _aarav_ma(mod, ticker, "sma", "SMA_WINDOWS", "fetch_sma_series", "sma_map")


def _aarav4(mod: ModuleType, ticker: str, period: str) -> dict:
    return _aarav_ma(mod, ticker, "ema", "EMA_WINDOWS", "fetch_ema_series", "ema_map")


def _result(signal: str, ten: float, native_rating: str | None,
            breakdown: list[str], details: dict) -> dict:
    """Helper for signals already on a 1-10 scale."""
    ten = round(float(ten), 1)
    return {
        "signal": signal,
        "ten": ten,
        "native_score": fmt_ten(ten),
        "native_rating": native_rating or ten_to_label(ten),
        "breakdown": breakdown,
        "details": details,
    }


# ───────────────────────── samar: cross-sectional momentum ──────────────────
_SAMAR_UNIVERSE: dict = {}


def _samar_universe(mod: ModuleType):
    if "data" not in _SAMAR_UNIVERSE:
        snap = mod.get_market_snapshots()
        universe = list(mod.DIVERSE_UNIVERSE.keys())
        momentum_dict, _ = mod.compute_momentum(universe, snap)
        # Skip build_cap_tiers(): it scrapes constituents for S&P 500/400/600 +
        # Nasdaq-100 (~1600 tickers) and is by far the slowest step. The
        # cap-relative z it feeds is only a secondary tiebreaker — the 1-10
        # score and recommendation come from the diverse-universe z-scores.
        cap_tiers: dict = {}
        results, stats = mod.standardize(momentum_dict, mod.DIVERSE_UNIVERSE, cap_tiers)
        _SAMAR_UNIVERSE["data"] = (snap, momentum_dict, results, stats, cap_tiers)
    return _SAMAR_UNIVERSE["data"]


def _samar(mod: ModuleType, ticker: str, period: str) -> dict:
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
    raw = v["score_1_10"]
    sec = v.get("z_combined_sector")
    lines = [
        f"12-1 mom {v['m12_pct']:+.2f}% (z {v['z12']:+.2f})",
        f"6-1  mom {v['m6_pct']:+.2f}% (z {v['z6']:+.2f})",
        f"3-1  mom {v['m3_pct']:+.2f}% (z {v['z3']:+.2f})",
        f"Combined z {v['z_combined']:+.2f}  strong={v['is_strong']}  shape={v['shape']}"
        + (f"  sector-rel z {sec:+.2f}" if sec is not None else ""),
        f"Score {raw:.1f}/10 -> {v['recommendation']}",
    ]
    return _result("momentum", raw, v["recommendation"], lines,
                   {"score_1_10": raw, "z_combined": v["z_combined"]})


# ───────────────────────── justin: financial ratios ─────────────────────────
def _justin(mod: ModuleType, ticker: str, period: str) -> dict:
    res = mod.rate_stock(ticker)
    raw = res.get("score")
    if raw is None:
        raise ValueError(f"no ratios for {ticker}")
    lines = [f"Sector: {res.get('sector', '?')}  —  score {raw:.1f}/10"]
    for metric, info in (res.get("breakdown") or {}).items():
        try:
            lines.append(f"{metric}: {info.get('value'):.3f} -> {info.get('score'):.1f}/10")
        except (TypeError, ValueError):
            lines.append(f"{metric}: {info}")
    return _result("ratios", raw, None, lines,
                   {"score_1_10": raw, "sector": res.get("sector")})


# ───────────────────────── anshu: dividends (-15..21) ───────────────────────
def _anshu(mod: ModuleType, ticker: str, period: str) -> dict:
    earliest = date(2015, 1, 1)
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 730))
    if start < earliest:
        start = earliest
    res = mod.rate_ticker(ticker, start.isoformat(), end.isoformat())
    sig = res.get("signal")
    if sig in ("N/A", "ERROR", "NO DIVIDEND") or res.get("score") is None:
        raise ValueError("; ".join(res.get("reasoning") or []) or str(sig))
    # anshu's rate_ticker already returns `score` clamped to 1-10 (with its own
    # signal thresholds), so pass it straight through — do NOT re-map a range.
    raw = res.get("score", 0)
    ten = round(max(1.0, min(10.0, float(raw))), 1)
    head = f"Dividend score {ten}/10  (signal {sig})"
    if res.get("payout_ratio") is not None:
        head += f"  ·  payout {res['payout_ratio']}%"
    lines = [head, *(res.get("reasoning") or [])]
    return _result("dividends", ten, sig, lines,
                   {"score_1_10": ten, "signal": sig, "payout_ratio": res.get("payout_ratio")})


# ───────────────────────── anshu2: short interest (-8..12) ──────────────────
def _anshu2(mod: ModuleType, ticker: str, period: str) -> dict:
    # anshu2 caps its lookback at 1 year ("short interest is a short-term
    # indicator"), so keep the window safely under 365 days regardless of period.
    end = date.today()
    start = end - timedelta(days=min(period_to_days(period), 360))
    res = mod.rate_ticker(ticker, start.isoformat(), end.isoformat())
    sig = res.get("signal")
    if sig in ("N/A", "NO DATA", "ERROR"):
        raise ValueError("; ".join(res.get("reasoning") or []) or str(sig))
    raw = res.get("score", 0)
    ten = to_ten(raw, -8, 12)
    head = f"Native score {raw}  ->  {ten}/10  (signal {sig})"
    if res.get("short_pct_float") is not None:
        head += f"  short {res['short_pct_float']}% of float"
    if res.get("days_to_cover") is not None:
        head += f"  days-to-cover {res['days_to_cover']}"
    if res.get("squeeze_alert"):
        head += "  squeeze alert"
    lines = [head, *(res.get("reasoning") or [])]
    return _converted("short_interest", ten, sig, lines,
                      {"raw_score": raw, "signal": sig,
                       "short_pct_float": res.get("short_pct_float")})


# ───────────────────────── diya: liquidity (0..1) ───────────────────────────
def _diya(mod: ModuleType, ticker: str, period: str) -> dict:
    # diya hardcodes a placeholder MASSIVE_API_KEY ("YOUR_API_KEY_HERE") and
    # never reads the env; inject the real key into its module globals.
    key = os.environ.get("MASSIVE_API_KEY")
    if key:
        mod.MASSIVE_API_KEY = key
        mod.HEADERS = {"Authorization": f"Bearer {key}"}
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 1095))
    res = mod.liquidity_analysis(ticker, start.isoformat(), end.isoformat(), save_detail=False)
    if res.get("signal") == "NO_DATA" or res.get("composite_score") is None:
        raise ValueError("no liquidity data (Massive Advanced tier required)")
    comp = res["composite_score"]
    ten = to_ten(comp, 0, 1)
    if not is_scored(ten):
        raise ValueError("no liquidity data (Massive Advanced tier required)")
    op, fin = res.get("operational_score"), res.get("financial_score")
    lines = [
        f"Native composite {comp:.2f} on 0..1  ->  {ten}/10  (signal {res.get('signal')})",
        (f"Operational OCF {op:.2f} x0.4  +  Financial FCF {fin:.2f} x0.6"
         if op is not None and fin is not None else "sub-scores unavailable"),
        f"Based on {res.get('n_periods')} annual period(s)",
    ]
    return _converted("liquidity", ten, res.get("signal"), lines,
                      {"signal": res.get("signal"), "composite_score": comp})


def _converted(signal: str, ten: float | None, native_rating: str | None,
               breakdown: list[str], details: dict) -> dict:
    """Helper for signals converted from a native range onto 1-10."""
    return {
        "signal": signal,
        "ten": ten,
        "native_score": fmt_ten(ten),
        "native_rating": native_rating or ten_to_label(ten),
        "breakdown": breakdown,
        "details": details,
    }


# ───────────────────────── cosmo: insider sentiment ─────────────────────────
def _cosmo(mod: ModuleType, ticker: str, period: str) -> dict:
    key = os.environ.get("MASSIVE_API_KEY")
    if key:
        mod.API_KEY = key  # cosmo hardcodes a key; prefer the env one if set
    end = date.today()
    start = end - timedelta(days=max(period_to_days(period), 180))
    txns = mod.fetch_transactions(ticker, start.isoformat(), end.isoformat(), 200)
    res = mod.compute_signal(txns, ticker)
    sig = res.get("signal", "NEUTRAL")
    ten = insider_to_ten(sig)
    lines = [
        f"Insider signal {sig}  ->  {fmt_ten(ten)} for composite",
        res.get("reason", ""),
    ]
    return {
        "signal": "insider",
        "ten": ten,
        "native_score": f"{fmt_ten(ten)} ({sig})",
        "native_rating": sig,
        "breakdown": lines,
        "details": {"signal": sig, "reason": res.get("reason")},
    }


ADAPTERS: dict[str, dict] = {
    "samar": {"name": "momentum", "owner": "samar", "category": "Technicals", "analyze": _samar},
    "aarav": {"name": "macd", "owner": "aarav", "category": "Technicals", "analyze": _aarav},
    "aarav2": {"name": "rsi", "owner": "aarav2", "category": "Technicals", "analyze": _aarav2},
    "aarav3": {"name": "sma", "owner": "aarav3", "category": "Technicals", "analyze": _aarav3},
    "aarav4": {"name": "ema", "owner": "aarav4", "category": "Technicals", "analyze": _aarav4},
    "justin": {"name": "ratios", "owner": "justin", "category": "Fundamentals", "analyze": _justin},
    "anshu": {"name": "dividends", "owner": "anshu", "category": "Fundamentals", "analyze": _anshu},
    "diya": {"name": "liquidity", "owner": "diya", "category": "Fundamentals", "analyze": _diya},
    "anshu2": {"name": "short_interest", "owner": "anshu2", "category": "Sentiment", "analyze": _anshu2},
    "cosmo": {"name": "insider", "owner": "cosmo", "category": "Sentiment", "analyze": _cosmo},
}
