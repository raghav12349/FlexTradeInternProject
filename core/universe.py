"""Resolve a user's input into a list of tickers.

Accepts either:
  * a free-text list of tickers  ("AAPL NVDA, TSLA")
  * the name of a predefined universe / index / sector ("DOW30", "TECH")

These baskets are curated starting points so the recommender has plenty to rank
across indices and sectors. They can be extended or replaced with live index/
sector constituents from Massive reference data later.
"""
from __future__ import annotations

import re

# ── Indices ──────────────────────────────────────────────────────────────────
DOW30 = ["AAPL", "MSFT", "JPM", "V", "WMT", "UNH", "HD", "PG", "JNJ", "CRM",
         "KO", "CSCO", "MCD", "CAT", "AXP", "GS", "IBM", "DIS", "AMGN", "VZ",
         "HON", "NKE", "BA", "MMM", "TRV", "CVX", "MRK", "WBA", "DOW", "INTC"]

NASDAQ100 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
             "PEP", "COST", "ADBE", "NFLX", "AMD", "INTC", "CSCO", "QCOM",
             "TXN", "AMAT", "INTU", "BKNG", "MU", "LRCX", "ADI", "REGN",
             "VRTX", "PANW", "KLAC", "SNPS", "CDNS", "MRVL"]

SP_SAMPLE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK.B", "TSLA",
             "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX",
             "LLY", "ABBV", "BAC", "KO", "PEP", "COST", "MRK", "AVGO", "ORCL",
             "CRM", "ACN", "MCD"]

# ── Sectors ──────────────────────────────────────────────────────────────────
TECH = ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "CSCO",
        "ACN", "INTC", "QCOM", "TXN", "IBM", "NOW", "INTU", "AMAT", "MU"]

ENERGY = ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY",
          "WMB", "KMI", "HAL", "DVN", "HES", "BKR"]

FINANCE = ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "SPGI",
           "CB", "PGR", "USB", "PNC", "TFC"]

HEALTHCARE = ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR",
              "BMY", "AMGN", "CVS", "MDT", "GILD", "ISRG"]

CONSUMER = ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG",
            "TGT", "GM", "F", "MAR", "CMG", "ROST"]

STAPLES = ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL", "KMB",
           "GIS", "KHC", "SYY", "STZ", "HSY"]

INDUSTRIALS = ["CAT", "HON", "BA", "GE", "UPS", "RTX", "UNP", "DE", "LMT",
               "MMM", "EMR", "ETN", "ITW", "CSX", "NSC"]

MATERIALS = ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "NUE", "DOW", "DD",
             "PPG", "ALB", "CTVA", "VMC", "MLM", "IP"]

UTILITIES = ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "ED", "PEG",
             "WEC", "ES", "AWK", "DTE", "PPL"]

REAL_ESTATE = ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR",
               "VICI", "AVB", "EQR", "EXR", "INVH", "ARE"]

COMMUNICATION = ["GOOGL", "META", "NFLX", "DIS", "VZ", "T", "TMUS", "CMCSA",
                 "CHTR", "EA", "TTWO", "WBD", "OMC", "LYV", "PARA"]

MEGACAP = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK.B",
           "LLY", "AVGO"]

UNIVERSES: dict[str, list[str]] = {
    # indices
    "DOW30": DOW30, "NASDAQ100": NASDAQ100, "SP500": SP_SAMPLE, "MEGACAP": MEGACAP,
    # sectors
    "TECH": TECH, "ENERGY": ENERGY, "FINANCE": FINANCE, "HEALTHCARE": HEALTHCARE,
    "CONSUMER": CONSUMER, "STAPLES": STAPLES, "INDUSTRIALS": INDUSTRIALS,
    "MATERIALS": MATERIALS, "UTILITIES": UTILITIES, "REAL_ESTATE": REAL_ESTATE,
    "COMMUNICATION": COMMUNICATION,
}


def available() -> list[str]:
    """Names of the predefined universes (for a dropdown)."""
    return list(UNIVERSES)


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
