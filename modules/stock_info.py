"""Basic stock reference + price info, and search-by-name.

A small utility (not a scored signal) used by the dashboard to show what a
ticker actually is and to let users search by company name. Data comes from the
shared Massive REST client (reads MASSIVE_API_KEY).

    from modules.stock_info import get_info, get_ohlc, search
    info = get_info("AAPL")            # name, description, sector, industry, ...
    bars = get_ohlc("AAPL", days=30)   # recent daily OHLC
    hits = search("apple")             # [{"ticker": "AAPL", "name": "Apple Inc."}, ...]
"""
from __future__ import annotations

from datetime import date, timedelta

from core.massive import aggregates, get

# Coarse SIC-range -> sector, so we can show a sector alongside the API's
# finer-grained industry (sic_description).
_SIC_SECTORS: list[tuple[int, int, str]] = [
    (100, 999, "Agriculture"), (1000, 1499, "Mining"), (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"), (4000, 4999, "Transportation & Utilities"),
    (5000, 5199, "Wholesale Trade"), (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance & Real Estate"), (7000, 8999, "Services"),
]


def _sector_from_sic(sic) -> str:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unknown"
    for lo, hi, name in _SIC_SECTORS:
        if lo <= code <= hi:
            return name
    return "Unknown"


def get_info(ticker: str) -> dict:
    """Reference details for a ticker: name, description, sector, industry, etc."""
    data = get(f"/v3/reference/tickers/{ticker.upper()}")
    r = data.get("results") or {}
    return {
        "ticker": (r.get("ticker") or ticker).upper(),
        "name": r.get("name") or ticker.upper(),
        "description": r.get("description") or "",
        "sector": _sector_from_sic(r.get("sic_code")),
        "industry": r.get("sic_description") or "Unknown",
        "exchange": r.get("primary_exchange") or "",
        "market_cap": r.get("market_cap"),
        "homepage": r.get("homepage_url") or "",
        "employees": r.get("total_employees"),
    }


def get_ohlc(ticker: str, days: int = 30) -> list[dict]:
    """Recent daily OHLC bars (most recent last). Each: date, o, h, l, c, v."""
    end = date.today()
    start = end - timedelta(days=days * 2 + 10)  # pad for weekends/holidays
    bars = aggregates(ticker, multiplier=1, timespan="day",
                      from_=start.isoformat(), to=end.isoformat())
    out = []
    for b in bars[-days:]:
        out.append({"o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
                    "c": b.get("c"), "v": b.get("v"), "t": b.get("t")})
    return out


def latest_ohlc(ticker: str) -> dict | None:
    bars = get_ohlc(ticker, days=1)
    return bars[-1] if bars else None


def search(query: str, limit: int = 10) -> list[dict]:
    """Search tickers by company name or symbol. Returns [{ticker, name}, ...]."""
    q = (query or "").strip()
    if not q:
        return []
    data = get("/v3/reference/tickers",
               params={"search": q, "active": "true", "market": "stocks", "limit": limit})
    hits = []
    for r in data.get("results") or []:
        hits.append({"ticker": (r.get("ticker") or "").upper(),
                     "name": r.get("name") or ""})
    return hits


def _fmt_cap(mcap) -> str:
    if not mcap:
        return "N/A"
    mcap = float(mcap)
    if mcap >= 1e12:
        return f"${mcap/1e12:.2f}T"
    if mcap >= 1e9:
        return f"${mcap/1e9:.1f}B"
    if mcap >= 1e6:
        return f"${mcap/1e6:.0f}M"
    return f"${mcap:.0f}"


if __name__ == "__main__":
    import sys
    from core.env import load_local_keys
    load_local_keys()
    arg = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    if arg.startswith("?"):
        for hit in search(arg[1:]):
            print(f"  {hit['ticker']:<8} {hit['name']}")
    else:
        info = get_info(arg)
        print(f"\n{info['name']} ({info['ticker']})  —  {info['exchange']}")
        print(f"Sector: {info['sector']}   Industry: {info['industry']}")
        print(f"Market cap: {_fmt_cap(info['market_cap'])}")
        if info["description"]:
            print(f"\n{info['description'][:400]}")
        bar = latest_ohlc(arg)
        if bar:
            print(f"\nLatest OHLC: O {bar['o']}  H {bar['h']}  L {bar['l']}  C {bar['c']}  V {bar['v']}")
