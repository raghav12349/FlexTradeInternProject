"""Thin client for the Massive.com REST API.

Each per-person module is expected to do its own data collection — this is
just a convenience so the API key, base URL, and retry/error handling live
in one place. Use it or call `requests` directly; both are fine.

    export MASSIVE_API_KEY=...

Common usage:

    from core.massive import aggregates
    bars = aggregates("AAPL", multiplier=1, timespan="day",
                      from_="2024-01-01", to="2025-01-01")
    closes = [b["c"] for b in bars]
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import requests

BASE_URL = "https://api.massive.com"
_TIMEOUT = 30.0


def _api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise RuntimeError("MASSIVE_API_KEY env var is not set")
    return key


def get(path: str, params: dict[str, Any] | None = None) -> dict:
    """GET {BASE_URL}{path} with the API key attached, return parsed JSON."""
    url = path if path.startswith("http") else f"{BASE_URL}{path}"
    qp = dict(params or {})
    qp["apiKey"] = _api_key()
    r = requests.get(url, params=qp, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def aggregates(
    ticker: str,
    *,
    multiplier: int = 1,
    timespan: str = "day",
    from_: str,
    to: str,
    adjusted: bool = True,
    limit: int = 50000,
) -> list[dict]:
    """OHLCV bars for `ticker` between `from_` and `to` (YYYY-MM-DD).

    Returns the `results` list. Each bar has keys:
        t (ms epoch), o, h, l, c, v, vw (vwap), n (trade count).
    """
    path = f"/v2/aggs/ticker/{ticker.upper()}/range/{multiplier}/{timespan}/{from_}/{to}"
    data = get(path, params={
        "adjusted": "true" if adjusted else "false",
        "sort": "asc",
        "limit": limit,
    })
    return data.get("results") or []


def period_to_days(period: str) -> int:
    """Map yfinance-style period strings to a day count.

    Examples: "1y" -> 365, "6mo" -> 180, "90d" -> 90.
    """
    p = period.strip().lower()
    if p.endswith("y"):
        return int(float(p[:-1]) * 365)
    if p.endswith("mo"):
        return int(float(p[:-2]) * 30)
    if p.endswith("d"):
        return int(p[:-1])
    raise ValueError(f"unrecognized period: {period!r}")


def date_range(period: str, today: date | None = None) -> tuple[str, str]:
    """Return (from, to) ISO date strings spanning `period` ending today."""
    end = today or date.today()
    start = end - timedelta(days=period_to_days(period))
    return start.isoformat(), end.isoformat()
