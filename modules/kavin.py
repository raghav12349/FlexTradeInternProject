"""Kavin — SMA Crossover signal (20-day vs 50-day).
Fetches the fast and slow SMA from the Massive REST API and maps the
percentage spread onto [-1, 1].  Positive = fast above slow (bullish);
negative = fast below slow (bearish).  Score saturates at ±1 when the
spread reaches ±5 %.
"""
from __future__ import annotations

import sys
import warnings
from typing import Optional

warnings.filterwarnings("ignore", category=Warning, module="urllib3")

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: run  pip install requests")

from core.rating import score_to_rating

# ── Signal metadata ────────────────────────────────────────────────────────────
SIGNAL_NAME     = "sma_crossover"
SIGNAL_OWNER    = "kavin"
SIGNAL_CATEGORY = "Technicals"

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL       = "https://api.massive.com"
SMA_ENDPOINT   = "/v1/indicators/sma/{ticker}"

FAST_WINDOW    = 20    # days
SLOW_WINDOW    = 50    # days

# Spread at which the score saturates at ±1.
# A 5 % gap between the 20-day and 50-day SMA is treated as maximum conviction.
SATURATION_PCT = 0.05


# ── Helpers ───────────────────────────────────────────────────────────────────

API_KEY = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"

def _get_api_key() -> str:
    return API_KEY


def _fetch_sma(ticker: str, window: int, api_key: str) -> Optional[float]:
    """Fetch the most recent SMA value for *ticker* using *window* trading days."""
    url = BASE_URL + SMA_ENDPOINT.format(ticker=ticker.upper())
    params = {
        "timespan":    "day",
        "window":      window,
        "series_type": "close",
        "order":       "desc",
        "limit":       1,
        "adjusted":    "true",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=15)

    if not resp.ok:
        try:
            msg = resp.json().get("message") or resp.text
        except Exception:
            msg = resp.text
        raise RuntimeError(
            f"Massive API error {resp.status_code} "
            f"(window={window}, ticker={ticker}): {msg}"
        )

    data   = resp.json()
    values = (data.get("results") or {}).get("values") or []
    if not values:
        return None

    return float(values[0]["value"])


def _spread_to_score(spread_pct: float) -> float:
    """Map a percentage spread onto [-1, 1], saturating at ±SATURATION_PCT."""
    return max(-1.0, min(1.0, spread_pct / SATURATION_PCT))


# ── Public contract ───────────────────────────────────────────────────────────

def analyze(ticker: str, period: str = "2y", **_) -> dict:
    api_key = _get_api_key()
    ticker  = ticker.upper()

    sma_fast = _fetch_sma(ticker, FAST_WINDOW, api_key)
    sma_slow = _fetch_sma(ticker, SLOW_WINDOW, api_key)

    # Handle missing data gracefully
    if sma_fast is None or sma_slow is None:
        return {
            "ticker":  ticker,
            "signal":  SIGNAL_NAME,
            "score":   0.0,
            "rating":  score_to_rating(0.0),
            "details": {
                "sma_fast":       sma_fast,
                "sma_slow":       sma_slow,
                "spread_pct":     None,
                "interpretation": "Insufficient data to compute SMA crossover.",
            },
        }

    # Core calculation
    spread_pct = (sma_fast - sma_slow) / sma_slow
    score      = round(_spread_to_score(spread_pct), 4)

    # Human-readable interpretation
    direction = "above" if sma_fast > sma_slow else "below"
    if score > 0.2:
        momentum = "Bullish momentum — fast MA leading slow MA."
    elif score < -0.2:
        momentum = "Bearish momentum — fast MA lagging slow MA."
    else:
        momentum = "Near crossover — no strong directional conviction."

    interp = (
        f"The {FAST_WINDOW}-day SMA (${sma_fast:,.2f}) is "
        f"{abs(spread_pct)*100:.2f}% {direction} "
        f"the {SLOW_WINDOW}-day SMA (${sma_slow:,.2f}). {momentum}"
    )

    return {
        "ticker":  ticker,
        "signal":  SIGNAL_NAME,
        "score":   score,
        "rating":  score_to_rating(score),
        "details": {
            "sma_fast":       round(sma_fast, 4),
            "sma_slow":       round(sma_slow, 4),
            "spread_pct":     round(spread_pct * 100, 4),  # expressed as %
            "interpretation": interp,
        },
    }
