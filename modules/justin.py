"""
financial_ratios_rater.py

Rate a stock 1–10 based on sector-appropriate financial ratios from
the Massive.com REST API.

Usage (import):
    from financial_ratios_rater import rate_stock
    result = rate_stock("AAPL")
    print(result)

Usage (CLI):
    python financial_ratios_rater.py AAPL
    python financial_ratios_rater.py JPM Financials       # override sector
"""

from __future__ import annotations

import sys
import time
import json
from dataclasses import dataclass
from typing import Optional

from massive import RESTClient

API_KEY = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"
RATE_LIMIT_BACKOFF = 60  # seconds to wait after a 429


# ── SIC → Sector ─────────────────────────────────────────────────────────────
# Rules are evaluated in order; first match wins. Specific ranges come first.

_SIC_RULES: list[tuple[int, int, str]] = [
    (2830, 2836, "Healthcare"),           # Pharmaceutical preparations
    (3841, 3851, "Healthcare"),           # Surgical & medical instruments
    (8011, 8099, "Healthcare"),           # Hospitals & health services
    (7370, 7379, "Technology"),           # Computer programming & data processing
    (3670, 3699, "Technology"),           # Electronic components
    (3570, 3579, "Technology"),           # Computer & office equipment
    (4810, 4899, "Communication Services"),
    (4900, 4991, "Utilities"),
    (6500, 6552, "Real Estate"),
    (6000, 6411, "Financials"),           # Banks, brokers, insurance
    (6552, 6799, "Financials"),           # Holding companies, investment offices
    (1311, 1389, "Energy"),               # Oil & gas extraction + field services
    (2911, 2911, "Energy"),               # Petroleum refining
    (5400, 5499, "Consumer Staples"),     # Food stores
    (5900, 5963, "Consumer Staples"),     # Drug & grocery stores
    (2000, 2199, "Consumer Staples"),     # Food & tobacco manufacturing
    (2800, 2999, "Materials"),            # Chemicals (pharma handled above)
    (1000, 1499, "Materials"),            # Mining
    (2400, 2499, "Materials"),            # Lumber & wood
    (2600, 2699, "Materials"),            # Paper
    (3300, 3399, "Materials"),            # Primary metals
    (3400, 3499, "Industrials"),          # Fabricated metal products
    (3500, 3569, "Industrials"),          # Industrial machinery
    (3700, 3769, "Consumer Discretionary"),  # Motor vehicles
    (5200, 5999, "Consumer Discretionary"),  # Retail trade
    (7000, 7399, "Consumer Discretionary"),  # Hotels, amusement, auto services
    (4000, 4799, "Industrials"),          # Transportation
    (7400, 8999, "Industrials"),          # Business, engineering, mgmt services
]


def _sic_to_sector(sic: int) -> str:
    for lo, hi, sector in _SIC_RULES:
        if lo <= sic <= hi:
            return sector
    return "Industrials"


# ── Ratio scoring config ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricCfg:
    direction: str   # "low" = lower is better (P/E, D/E), "high" = higher is better (ROE)
    good: float      # threshold for a score of 10
    bad: float       # threshold for a score of 1
    weight: float


# Percentage-based ratios (ROE, ROA, dividend_yield) are stored as decimals by
# the API (e.g. 0.15 = 15%). We multiply by 100 before scoring, so all
# thresholds below use whole-number percentages.

SECTOR_PROFILES: dict[str, dict[str, MetricCfg]] = {
    "Technology": {
        "price_to_earnings": MetricCfg("low",  good=15.0, bad=45.0, weight=0.15),
        "revenue_growth":    MetricCfg("high", good=25.0, bad= 0.0, weight=0.25),
        "return_on_equity":  MetricCfg("high", good=20.0, bad= 6.0, weight=0.20),
        "debt_to_equity":    MetricCfg("low",  good= 0.3, bad= 2.0, weight=0.25),
        "ev_to_sales":       MetricCfg("low",  good= 2.0, bad=15.0, weight=0.15),
    },
    "Healthcare": {
        "price_to_earnings": MetricCfg("low",  good=15.0, bad=45.0, weight=0.20),
        "price_to_sales":    MetricCfg("low",  good= 2.0, bad=10.0, weight=0.15),
        "return_on_equity":  MetricCfg("high", good=18.0, bad= 5.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.3, bad= 1.5, weight=0.20),
        "quick":             MetricCfg("high", good= 2.5, bad= 1.0, weight=0.20),
    },
    "Financials": {
        "price_to_book":     MetricCfg("low",  good= 0.8, bad= 2.5, weight=0.30),
        "return_on_equity":  MetricCfg("high", good=15.0, bad= 5.0, weight=0.35),
        "return_on_assets":  MetricCfg("high", good= 1.5, bad= 0.3, weight=0.20),
        "price_to_earnings": MetricCfg("low",  good= 8.0, bad=20.0, weight=0.15),
    },
    "Energy": {
        "ev_to_ebitda":       MetricCfg("low",  good= 4.0, bad=13.0, weight=0.25),
        "price_to_cash_flow": MetricCfg("low",  good= 5.0, bad=16.0, weight=0.25),
        "debt_to_equity":     MetricCfg("low",  good= 0.5, bad= 2.0, weight=0.20),
        "dividend_yield":     MetricCfg("high", good= 5.0, bad= 1.5, weight=0.15),
        "return_on_equity":   MetricCfg("high", good=15.0, bad= 3.0, weight=0.15),
    },
    "Consumer Discretionary": {
        "price_to_earnings": MetricCfg("low",  good=15.0, bad=35.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.5, bad= 2.0, weight=0.25),
        "return_on_equity":  MetricCfg("high", good=20.0, bad= 6.0, weight=0.25),
        "current":           MetricCfg("high", good= 2.0, bad= 1.0, weight=0.25),
    },
    "Consumer Staples": {
        "price_to_earnings": MetricCfg("low",  good=15.0, bad=30.0, weight=0.20),
        "dividend_yield":    MetricCfg("high", good= 3.5, bad= 1.0, weight=0.20),
        "debt_to_equity":    MetricCfg("low",  good= 0.5, bad= 2.0, weight=0.20),
        "return_on_equity":  MetricCfg("high", good=20.0, bad= 6.0, weight=0.20),
        "current":           MetricCfg("high", good= 2.0, bad= 1.0, weight=0.20),
    },
    "Industrials": {
        "price_to_earnings": MetricCfg("low",  good=12.0, bad=30.0, weight=0.20),
        "ev_to_ebitda":      MetricCfg("low",  good= 8.0, bad=22.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.5, bad= 2.0, weight=0.25),
        "return_on_assets":  MetricCfg("high", good=10.0, bad= 3.0, weight=0.15),
        "current":           MetricCfg("high", good= 2.0, bad= 1.0, weight=0.15),
    },
    "Materials": {
        "ev_to_ebitda":      MetricCfg("low",  good= 6.0, bad=20.0, weight=0.30),
        "price_to_book":     MetricCfg("low",  good= 1.0, bad= 4.0, weight=0.20),
        "return_on_equity":  MetricCfg("high", good=15.0, bad= 5.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.4, bad= 1.8, weight=0.25),
    },
    "Real Estate": {
        "dividend_yield":    MetricCfg("high", good= 5.0, bad= 2.0, weight=0.30),
        "ev_to_ebitda":      MetricCfg("low",  good=15.0, bad=32.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.8, bad= 2.5, weight=0.25),
        "price_to_book":     MetricCfg("low",  good= 1.0, bad= 3.0, weight=0.20),
    },
    "Utilities": {
        "price_to_earnings": MetricCfg("low",  good=12.0, bad=25.0, weight=0.20),
        "dividend_yield":    MetricCfg("high", good= 4.5, bad= 2.0, weight=0.30),
        "debt_to_equity":    MetricCfg("low",  good= 1.0, bad= 3.0, weight=0.25),
        "ev_to_ebitda":      MetricCfg("low",  good= 8.0, bad=18.0, weight=0.25),
    },
    "Communication Services": {
        "ev_to_ebitda":      MetricCfg("low",  good= 8.0, bad=22.0, weight=0.25),
        "ev_to_sales":       MetricCfg("low",  good= 1.0, bad= 8.0, weight=0.20),
        "return_on_equity":  MetricCfg("high", good=15.0, bad= 5.0, weight=0.25),
        "debt_to_equity":    MetricCfg("low",  good= 0.5, bad= 2.5, weight=0.15),
        "price_to_earnings": MetricCfg("low",  good=12.0, bad=30.0, weight=0.15),
    },
}

DEFAULT_PROFILE: dict[str, MetricCfg] = {
    "price_to_earnings": MetricCfg("low",  good=15.0, bad=40.0, weight=0.30),
    "debt_to_equity":    MetricCfg("low",  good= 0.5, bad= 2.0, weight=0.25),
    "return_on_equity":  MetricCfg("high", good=15.0, bad= 5.0, weight=0.25),
    "current":           MetricCfg("high", good= 2.0, bad= 1.0, weight=0.20),
}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_metric(value: float, cfg: MetricCfg) -> float:
    """Return a 1–10 score by linearly interpolating between good and bad."""
    # lo=bad (score 1), hi=good (score 10). Works for both directions because
    # "low" metrics have bad > good, so the slope naturally inverts.
    lo, hi = cfg.bad, cfg.good
    return max(1.0, min(10.0, 1.0 + 9.0 * (value - lo) / (hi - lo)))


def _compute_score(
    ratios: dict[str, float],
    profile: dict[str, MetricCfg],
) -> tuple[float, dict[str, dict]]:
    """Compute a weighted 1–10 score across all available ratios."""
    breakdown: dict[str, dict] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for name, cfg in profile.items():
        raw = ratios.get(name)
        if raw is None:
            continue

        # Negative value on a metric that shouldn't be negative → worst score
        score = 1.0 if raw < 0 else _score_metric(raw, cfg)

        breakdown[name] = {
            "value": round(raw, 4),
            "score": round(score, 2),
            "weight": cfg.weight,
        }
        weighted_sum += score * cfg.weight
        total_weight += cfg.weight

    if total_weight == 0:
        return 5.0, {}  # no data → neutral

    final = weighted_sum / total_weight
    return round(max(1.0, min(10.0, final)), 2), breakdown


# ── API helpers ───────────────────────────────────────────────────────────────

def _call_with_retry(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if "429" not in str(e):
            raise
        print(f"rate limited — waiting {RATE_LIMIT_BACKOFF}s...", end=" ", flush=True)
        time.sleep(RATE_LIMIT_BACKOFF)
        return fn(*args, **kwargs)


_PCT_FIELDS = {"return_on_equity", "return_on_assets", "dividend_yield"}


def _fetch_ratios(client: RESTClient, ticker: str) -> dict[str, float]:
    results = list(_call_with_retry(client.list_financials_ratios, ticker=ticker, limit=1))
    if not results:
        raise ValueError(f"No ratio data returned for '{ticker}'")
    r = results[0]
    ratios: dict[str, float] = {}
    for field in (
        "price_to_earnings", "price_to_book", "price_to_sales",
        "price_to_cash_flow", "price_to_free_cash_flow",
        "return_on_equity", "return_on_assets", "dividend_yield",
        "debt_to_equity", "current", "quick", "cash",
        "ev_to_sales", "ev_to_ebitda",
    ):
        val = getattr(r, field, None)
        if val is not None:
            # ROE, ROA, dividend_yield come back as decimals (0.15 = 15%)
            ratios[field] = float(val) * 100 if field in _PCT_FIELDS else float(val)
    return ratios


# Stocks where the SEC's SIC code produces the wrong sector.
# Checked against actual SIC codes returned by the Massive API.
_SECTOR_OVERRIDES: dict[str, str] = {
    # Crypto mining — SEC assigns SIC 6199 (Finance Services)
    "CORZ": "Technology",
    "MARA": "Technology",
    "RIOT": "Technology",
    "CLSK": "Technology",
    "HUT":  "Technology",
    "BITF": "Technology",
    # Social media & search — SIC 7370 maps to Technology, GICS is Comm. Services
    "META":  "Communication Services",
    "GOOGL": "Communication Services",
    "GOOG":  "Communication Services",
    "SNAP":  "Communication Services",
    "PINS":  "Communication Services",
    "RDDT":  "Communication Services",
    # Streaming & entertainment — SIC puts them in Industrials
    "NFLX": "Communication Services",
    "DIS":  "Communication Services",
    "WBD":  "Communication Services",
    "PARA": "Communication Services",
    # Payment networks & fintech — SIC 7389 maps to Consumer Discretionary
    "V":    "Financials",
    "MA":   "Financials",
    "PYPL": "Financials",
    "GPN":  "Financials",
    "FIS":  "Financials",
    # E-commerce — SIC 5961 (Catalog & Mail-Order) maps to Consumer Staples
    "AMZN": "Consumer Discretionary",
    "EBAY": "Consumer Discretionary",
    "ETSY": "Consumer Discretionary",
}


def _fetch_revenue_growth(client: RESTClient, ticker: str) -> Optional[float]:
    """Return YoY annual revenue growth as a percentage, or None if unavailable."""
    rows = list(_call_with_retry(
        client.list_financials_income_statements,
        tickers=ticker,
        timeframe="annual",
        limit=10,
    ))
    rows.sort(key=lambda r: r.period_end, reverse=True)
    if len(rows) < 2:
        return None
    rev_new = getattr(rows[0], "revenue", None)
    rev_old = getattr(rows[1], "revenue", None)
    if not rev_new or not rev_old or rev_old == 0:
        return None
    return (rev_new - rev_old) / abs(rev_old) * 100


def _fetch_sector(client: RESTClient, ticker: str) -> str:
    if ticker in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[ticker]
    details = _call_with_retry(client.get_ticker_details, ticker)
    sic = getattr(details, "sic_code", None)
    if sic is None:
        return "Industrials"
    return _sic_to_sector(int(sic))


# ── Public API ────────────────────────────────────────────────────────────────

def rate_stock(
    ticker: str,
    api_key: str = API_KEY,
    sector_override: Optional[str] = None,
) -> dict:
    """
    Rate a stock on a 1–10 scale using sector-appropriate financial ratios.

    Args:
        ticker:          Ticker symbol, e.g. "AAPL".
        api_key:         Massive.com API key.
        sector_override: Force a specific sector instead of auto-detecting from
                         the SIC code (e.g. "Technology", "Financials").

    Returns a dict:
        {
          "ticker":    "AAPL",
          "sector":    "Technology",
          "score":     7.2,
          "breakdown": {
            "price_to_earnings": {"value": 34.81, "score": 5.80, "weight": 0.20},
            ...
          }
        }
    """
    ticker = ticker.upper()
    client = RESTClient(api_key)

    sector = sector_override or _fetch_sector(client, ticker)
    config = SECTOR_PROFILES.get(sector, DEFAULT_PROFILE)
    ratios = _fetch_ratios(client, ticker)

    if "revenue_growth" in config:
        growth = _fetch_revenue_growth(client, ticker)
        if growth is not None:
            ratios["revenue_growth"] = growth

    score, breakdown = _compute_score(ratios, config)

    return {
        "ticker": ticker,
        "sector": sector,
        "score": score,
        "breakdown": breakdown,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stock_rater.py <TICKER> [sector_override]", file=sys.stderr)
        sys.exit(1)

    ticker_arg = sys.argv[1]
    sector_arg = sys.argv[2] if len(sys.argv) > 2 else None
    result = rate_stock(ticker_arg, sector_override=sector_arg)

    print(f"\n{'='*40}")
    print(f"  {result['ticker']}  |  Sector: {result['sector']}")
    print(f"  Score: {result['score']} / 10")
    print(f"{'='*40}")
    for name, info in result["breakdown"].items():
        bar = "█" * int(info["score"])
        print(f"  {name:<25} {info['value']:>10.3f}   {info['score']:>5.2f}/10  {bar}")
    print()
