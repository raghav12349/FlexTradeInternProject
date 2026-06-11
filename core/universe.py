"""Resolve a user's input into a list of tickers for the recommender.

`resolve()` accepts *anything* and sorts it out:

  * a free-text list of tickers            ("AAPL NVDA, TSLA")
  * an index name                          ("SP500", "S&P 500")
  * a sector name                          ("tech", "Information Technology",
                                            "healthcare", "energy", ...)

Index and sector membership is pulled **live from the web** (the S&P 500
constituents dataset, which carries each company's GICS sector) rather than
hardcoded, so the lists stay current and a sector name resolves to its real
constituents. The fetch is cached in-process; if the web is unreachable we fall
back to a small static basket so the app still works offline.

Single-equity analysis (the Single Ticker tab) is unaffected — this module only
feeds the multi-ticker recommender.
"""
from __future__ import annotations

import io
import re
import time

import requests

# Live source: S&P 500 constituents with a GICS Sector column. One fetch gives
# us the index *and* every sector's membership.
_SP500_CSV = ("https://raw.githubusercontent.com/datasets/"
              "s-and-p-500-companies/main/data/constituents.csv")
_CACHE_TTL = 24 * 3600  # seconds
_cache: dict[str, object] = {"baskets": None, "ts": 0.0}

# GICS sector (as it appears in the dataset) -> short canonical basket name we
# show in the dropdown.
_GICS_SHORT = {
    "Information Technology": "TECH",
    "Health Care": "HEALTHCARE",
    "Financials": "FINANCE",
    "Energy": "ENERGY",
    "Consumer Discretionary": "CONSUMER_DISCRETIONARY",
    "Consumer Staples": "STAPLES",
    "Industrials": "INDUSTRIALS",
    "Materials": "MATERIALS",
    "Utilities": "UTILITIES",
    "Real Estate": "REAL_ESTATE",
    "Communication Services": "COMMUNICATION",
}

# Free-text aliases a user might type -> canonical basket name.
_ALIASES = {
    "S&P500": "SP500", "S&P 500": "SP500", "SP 500": "SP500", "SPX": "SP500",
    "GSPC": "SP500", "S AND P 500": "SP500",
    "TECHNOLOGY": "TECH", "IT": "TECH", "INFORMATION TECHNOLOGY": "TECH",
    "HEALTH": "HEALTHCARE", "HEALTH CARE": "HEALTHCARE",
    "FINANCIAL": "FINANCE", "FINANCIALS": "FINANCE", "BANKS": "FINANCE",
    "CONSUMER DISCRETIONARY": "CONSUMER_DISCRETIONARY",
    "DISCRETIONARY": "CONSUMER_DISCRETIONARY", "CONSUMER": "CONSUMER_DISCRETIONARY",
    "CONSUMER STAPLES": "STAPLES",
    "INDUSTRIAL": "INDUSTRIALS",
    "MATERIAL": "MATERIALS",
    "UTILITY": "UTILITIES",
    "REAL ESTATE": "REAL_ESTATE", "REIT": "REAL_ESTATE", "REITS": "REAL_ESTATE",
    "COMMUNICATION SERVICES": "COMMUNICATION", "TELECOM": "COMMUNICATION",
    "COMMS": "COMMUNICATION",
}

# Offline fallback so the app still works with no network. Mega-caps + a couple
# of indices the live CSV doesn't cover; sectors fall back to short samples.
MEGACAP = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
           "LLY", "AVGO"]
DOW30 = ["AAPL", "MSFT", "JPM", "V", "WMT", "UNH", "HD", "PG", "JNJ", "CRM",
         "KO", "CSCO", "MCD", "CAT", "AXP", "GS", "IBM", "DIS", "AMGN", "VZ",
         "HON", "NKE", "BA", "MMM", "TRV", "CVX", "MRK", "WBA", "DOW", "INTC"]
_STATIC: dict[str, list[str]] = {
    "MEGACAP": MEGACAP,
    "DOW30": DOW30,
    "SP500": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK.B", "TSLA",
              "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX"],
    "TECH": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "CSCO"],
    "HEALTHCARE": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT"],
    "FINANCE": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP"],
    "ENERGY": ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO"],
    "CONSUMER_DISCRETIONARY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX"],
    "STAPLES": ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ"],
    "INDUSTRIALS": ["CAT", "HON", "BA", "GE", "UPS", "RTX", "UNP", "DE"],
    "MATERIALS": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "NUE", "DOW"],
    "UTILITIES": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL"],
    "REAL_ESTATE": ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL"],
    "COMMUNICATION": ["GOOGL", "META", "NFLX", "DIS", "VZ", "T", "TMUS", "CMCSA"],
}

# Order shown in the dropdown.
_BASKET_ORDER = ["SP500", "MEGACAP", "DOW30", "TECH", "HEALTHCARE", "FINANCE",
                 "ENERGY", "CONSUMER_DISCRETIONARY", "STAPLES", "INDUSTRIALS",
                 "MATERIALS", "UTILITIES", "REAL_ESTATE", "COMMUNICATION"]


def _fetch_live_baskets() -> dict[str, list[str]] | None:
    """Build {SP500 + each sector: [tickers]} from the live constituents CSV."""
    try:
        import pandas as pd
        resp = requests.get(_SP500_CSV, timeout=12)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = [str(s).strip().upper() for s in df["Symbol"] if str(s).strip()]
        baskets: dict[str, list[str]] = {"SP500": symbols}
        for gics, grp in df.groupby("GICS Sector"):
            short = _GICS_SHORT.get(str(gics).strip())
            if short:
                baskets[short] = [str(s).strip().upper() for s in grp["Symbol"]
                                  if str(s).strip()]
        return baskets
    except Exception:  # noqa: BLE001 - any failure -> fall back to static
        return None


def _baskets() -> dict[str, list[str]]:
    """Live baskets (cached, with TTL); static fallback if the web is down.

    Static baskets fill in any names the live source doesn't provide (e.g.
    MEGACAP, DOW30), so callers always get a complete set.
    """
    cached = _cache["baskets"]
    if cached is not None and time.time() - float(_cache["ts"]) < _CACHE_TTL:
        return cached  # type: ignore[return-value]
    live = _fetch_live_baskets()
    merged = dict(_STATIC)
    if live:
        merged.update(live)  # prefer live membership where available
    _cache["baskets"] = merged
    _cache["ts"] = time.time()
    return merged


def _canonical(name: str) -> str | None:
    """Map a typed name/alias to a canonical basket key, or None."""
    key = re.sub(r"\s+", " ", (name or "").strip().upper()).replace("-", "_")
    if key in _BASKET_ORDER:
        return key
    spaced = key.replace("_", " ")
    if spaced in _ALIASES:
        return _ALIASES[spaced]
    if key in _ALIASES:
        return _ALIASES[key]
    return None


def available() -> list[str]:
    """Basket names for the dropdown (no network — names are static)."""
    return list(_BASKET_ORDER)


def _dedup(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def resolve(query: str) -> list[str]:
    """Turn `query` into an ordered, de-duplicated list of ticker symbols.

    An index/sector name resolves to its live constituents; anything else is
    parsed as a whitespace/comma-separated list of tickers.
    """
    q = (query or "").strip()
    if not q:
        return []
    canon = _canonical(q)
    if canon:
        return _dedup(_baskets().get(canon, _STATIC.get(canon, [])))
    return _dedup(re.split(r"[,\s]+", q))
