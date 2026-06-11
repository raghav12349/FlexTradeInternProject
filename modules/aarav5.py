"""
aarav5.py — Cross-module stock analysis aggregator.

Combines MACD (aarav.py), RSI (aarav2.py), SMA (aarav3.py), EMA (aarav4.py)
into a single weighted composite score and final trader recommendation.

Weights (tuned for swing/position trading):
  SMA  30%  — baseline trend structure (slower, more reliable)
  EMA  25%  — faster trend confirmation
  RSI  25%  — momentum / mean-reversion timing
  MACD 20%  — momentum acceleration / signal confirmation

Output scale: 1–10
  ≥ 8.5  Strong Buy
  ≥ 6.5  Buy
  ≥ 4.5  Neutral / Hold
  ≥ 2.5  Sell / Avoid
  < 2.5  Strong Sell
"""

import os
import sys
from datetime import datetime, timedelta

import requests

try:
    from massive import RESTClient
except ImportError:
    from polygon import RESTClient

# ── Shared config ──────────────────────────────────────────────────────────────
def _load_api_key():
    k = os.environ.get("POLYGON_API_KEY")
    if k:
        return k
    import pathlib
    env_file = pathlib.Path(__file__).parent.parent / ".keys.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("POLYGON_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("POLYGON_API_KEY not set — add it to .keys.env or export it")

API_KEY  = _load_api_key()
BASE_URL = "https://api.polygon.io"
client   = RESTClient(API_KEY)

CAP_TIERS = [
    (200_000_000_000, "Mega Cap",  "Top-tier blue chips (>$200B)"),
    ( 10_000_000_000, "Large Cap", "$10B–$200B"),
    (  2_000_000_000, "Mid Cap",   "$2B–$10B"),
    (              0, "Small Cap", "<$2B"),
]

# Cross-module weights must sum to 1.0
MODULE_WEIGHTS = {
    "sma":  0.30,
    "ema":  0.25,
    "rsi":  0.25,
    "macd": 0.20,
}

# ── Score → label helpers (standardised across all aarav modules) ──────────────
def trend_label(score):
    if score >= 8: return "Strong Uptrend (Bullish)"
    if score >= 6: return "Moderate Uptrend"
    if score >= 5: return "Sideways / Neutral"
    if score >= 3: return "Moderate Downtrend"
    return "Strong Downtrend (Bearish)"


def buy_label(score):
    if score >= 8:   return "Strong Buy"
    if score >= 6:   return "Buy"
    if score >= 4.5: return "Neutral / Hold"
    if score >= 3:   return "Sell / Avoid"
    return "Strong Sell"


def _volume_confidence(volumes):
    """High/Normal/Low — 20d avg vs 90d avg volume."""
    dates = sorted(volumes.keys())
    if len(dates) < 20:
        return "Normal"
    v20 = sum(volumes[d] for d in dates[-20:]) / 20
    v90 = sum(volumes[d] for d in dates[-90:]) / min(90, len(dates))
    if v90 == 0:
        return "Normal"
    ratio = v20 / v90
    if ratio >= 1.15:
        return "High"
    if ratio <= 0.85:
        return "Low"
    return "Normal"


def _recency_surge(prices):
    """True if stock up >12% over last 20 trading days."""
    dates = sorted(prices.keys())
    if len(dates) < 20:
        return False
    p_now = prices[dates[-1]]
    p_20  = prices[dates[-20]]
    return p_20 > 0 and (p_now - p_20) / p_20 > 0.12


def _spy_bullish():
    """True if SPY is above its 50d SMA right now."""
    try:
        today = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=120)).strftime("%Y-%m-%d")
        bars  = client.get_aggs("SPY", 1, "day", start, today,
                                adjusted="true", sort="asc", limit=200)
        closes = [b.close for b in bars]
        return len(closes) >= 50 and closes[-1] > sum(closes[-50:]) / 50
    except Exception:
        return True  # fail open


def composite_label(comp, rsi_score, sma_score=None, volumes=None, prices=None):
    """
    Multi-gate composite label. Buy signals require all of:
      1. RSI >= 5          — momentum not bearish
      2. SMA >= 6.5        — structural trend stack confirmed
      3. SPY above 50d SMA — broad market not in downtrend
      4. Volume not Low    — institutional participation
      5. No recency surge  — not chasing extended move (>12% in 20d)
    """
    if comp is None:
        return None
    label = buy_label(comp)
    if label in ("Buy", "Strong Buy"):
        if rsi_score is not None and rsi_score < 5:
            return "Neutral / Hold"
        if sma_score is not None and sma_score < 6.5:
            return "Neutral / Hold"
        if not _spy_bullish():
            return "Neutral / Hold"
        if volumes is not None and _volume_confidence(volumes) == "Low":
            return "Neutral / Hold"
        if prices is not None and _recency_surge(prices):
            return "Neutral / Hold"
    return label


# ── Data fetchers (self-contained so aarav5 has no import-time side effects) ────

def classify_cap(market_cap):
    if market_cap is None:
        return "Unknown", "Market cap unavailable"
    for threshold, label, desc in CAP_TIERS:
        if market_cap >= threshold:
            return label, desc
    return "Small Cap", "<$2B"


def fetch_ticker_details(symbol):
    try:
        d = client.get_ticker_details(symbol)
        name = getattr(d, "name", symbol)
        market_cap = getattr(d, "market_cap", None)
    except Exception:
        name, market_cap = symbol, None
    cap_tier, cap_desc = classify_cap(market_cap)
    return {"name": name, "market_cap": market_cap, "cap_tier": cap_tier, "cap_desc": cap_desc}


def fetch_daily_prices(symbol, start_date, end_date):
    bars = client.get_aggs(
        ticker=symbol, multiplier=1, timespan="day",
        from_=start_date, to=end_date,
        adjusted="true", sort="asc", limit=50000,
    )
    if not bars:
        raise ValueError(f"No price data for {symbol}")
    prices, volumes = {}, {}
    for bar in bars:
        date = datetime.utcfromtimestamp(bar.timestamp / 1000).strftime("%Y-%m-%d")
        prices[date]  = bar.close
        volumes[date] = getattr(bar, "volume", 0) or 0
    return prices, volumes


def fetch_rsi_series(symbol, window=14, limit=500):
    url = f"{BASE_URL}/v1/indicators/rsi/{symbol}"
    r = requests.get(url, params={
        "timespan": "day", "adjusted": "true", "window": window,
        "series_type": "close", "order": "desc", "limit": limit, "apiKey": API_KEY,
    })
    return r.json().get("results", {}).get("values", [])


def fetch_sma_series(symbol, window, limit=260):
    sma = client.get_sma(
        ticker=symbol, timespan="day", adjusted="true",
        window=str(window), series_type="close", order="desc", limit=str(limit),
    )
    values = getattr(sma, "values", None) or []
    series = [{"timestamp": v.timestamp, "value": v.value} for v in values]
    series.reverse()
    return series


def fetch_ema_series(symbol, window, limit=260):
    ema = client.get_ema(
        ticker=symbol, timespan="day", adjusted="true",
        window=str(window), series_type="close", order="desc", limit=str(limit),
    )
    values = getattr(ema, "values", None) or []
    series = [{"timestamp": v.timestamp, "value": v.value} for v in values]
    series.reverse()
    return series


# ── Per-module score extractors ────────────────────────────────────────────────

def _score_macd(prices):
    """Return MACD composite score (1–10) from price history."""
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "aarav_macd",
        _os.path.join(_os.path.dirname(__file__), "aarav.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    macd_full = mod.compute_macd(prices)
    if macd_full is None:
        return None

    two_yr = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    scores = []
    for _days_pair in mod.TIMEFRAMES.values():
        _, days = _days_pair
        start = max((datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d"), two_yr)
        cnt = sum(1 for d in macd_full["dates"] if d >= start)
        if cnt < 5:
            continue
        s = mod.score_macd(macd_full, start)
        scores.append(s)
    return round(sum(scores) / len(scores), 1) if scores else None


def _score_rsi(symbol, prices):
    """Return RSI composite score (1–10)."""
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "aarav_rsi",
        _os.path.join(_os.path.dirname(__file__), "aarav2.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rsi_series = fetch_rsi_series(symbol)
    if not rsi_series:
        return None
    rsi_data = mod.analyze_rsi_series(rsi_series, prices_dict=prices)
    return round(rsi_data["score"], 1) if rsi_data else None


def _score_sma(symbol, prices, volumes):
    """Return SMA composite score (1–10)."""
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "aarav_sma",
        _os.path.join(_os.path.dirname(__file__), "aarav3.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    current_price, ma200, week52_high = mod.compute_context(prices)
    vol_profile = mod.compute_volume_profile(volumes) if volumes else {"confidence": "Normal"}
    sma_map = {}
    for w in mod.SMA_WINDOWS:
        sma_map[w] = fetch_sma_series(symbol, w)
    sma_data = mod.analyze_sma(sma_map, current_price, ma200, week52_high, vol_profile["confidence"])
    return round(sma_data["score"], 1) if sma_data else None


def _score_ema(symbol, prices, volumes):
    """Return EMA composite score (1–10)."""
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "aarav_ema",
        _os.path.join(_os.path.dirname(__file__), "aarav4.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    current_price, ma200, week52_high = mod.compute_context(prices)
    vol_profile = mod.compute_volume_profile(volumes) if volumes else {"confidence": "Normal"}
    ema_map = {}
    for w in mod.EMA_WINDOWS:
        ema_map[w] = fetch_ema_series(symbol, w)
    ema_data = mod.analyze_ema(ema_map, current_price, ma200, week52_high, vol_profile["confidence"])
    return round(ema_data["score"], 1) if ema_data else None


# ── Composite ─────────────────────────────────────────────────────────────────

def analyze(symbol, prices, volumes, ticker_details=None):
    """
    Run all four modules and combine into a single weighted composite.

    Returns a dict with per-module scores and the final recommendation.
    """
    td = ticker_details or {}

    print(f"  [MACD] computing...", end=" ", flush=True)
    macd_score = _score_macd(prices)
    print(f"{macd_score if macd_score else 'N/A'}")

    print(f"  [RSI]  computing...", end=" ", flush=True)
    rsi_score = _score_rsi(symbol, prices)
    print(f"{rsi_score if rsi_score else 'N/A'}")

    print(f"  [SMA]  computing...", end=" ", flush=True)
    sma_score = _score_sma(symbol, prices, volumes)
    print(f"{sma_score if sma_score else 'N/A'}")

    print(f"  [EMA]  computing...", end=" ", flush=True)
    ema_score = _score_ema(symbol, prices, volumes)
    print(f"{ema_score if ema_score else 'N/A'}")

    module_scores = {
        "macd": macd_score,
        "rsi":  rsi_score,
        "sma":  sma_score,
        "ema":  ema_score,
    }

    # Weighted composite — skip missing modules, reweight remaining
    total_weight = sum(MODULE_WEIGHTS[k] for k, v in module_scores.items() if v is not None)
    if total_weight == 0:
        composite = None
    else:
        composite = sum(
            module_scores[k] * MODULE_WEIGHTS[k]
            for k in module_scores if module_scores[k] is not None
        ) / total_weight

    comp_rounded = round(composite, 1) if composite is not None else None

    return {
        "symbol":  symbol.upper(),
        "company": td.get("name", symbol.upper()),
        "cap": {
            "tier":       td.get("cap_tier", "Unknown"),
            "description": td.get("cap_desc", ""),
            "market_cap": td.get("market_cap"),
        },
        "scores": module_scores,
        "composite": {
            "score":          comp_rounded,
            "trend":          trend_label(comp_rounded) if comp_rounded else "N/A",
            "recommendation": composite_label(
                comp_rounded, rsi_score,
                sma_score=sma_score, volumes=volumes, prices=prices,
            ) if comp_rounded else "N/A",
        } if comp_rounded is not None else None,
    }


def print_result(result):
    cap  = result.get("cap", {})
    comp = result.get("composite")
    scores = result.get("scores", {})

    mcap = cap.get("market_cap")
    mcap_str = f"${mcap/1e9:.1f}B" if mcap and mcap >= 1e9 else (f"${mcap/1e6:.0f}M" if mcap else "N/A")

    def bar(score, width=20):
        if score is None:
            return "N/A"
        filled = round(score / 10 * width)
        return "█" * filled + "░" * (width - filled) + f"  {score:.1f}/10"

    print(f"\n{'='*62}")
    print(f"  COMBINED ANALYSIS — {result['symbol']}  ({result.get('company', '')})")
    print(f"{'='*62}")
    print(f"  Cap Tier   : {cap.get('tier', 'Unknown')}  ({cap.get('description', '')})")
    print(f"  Market Cap : {mcap_str}")
    print(f"")
    print(f"  Module Scores (1–10):")
    print(f"  MACD  {bar(scores.get('macd'))}")
    print(f"  RSI   {bar(scores.get('rsi'))}")
    print(f"  SMA   {bar(scores.get('sma'))}")
    print(f"  EMA   {bar(scores.get('ema'))}")

    weights_str = "  Weights: SMA 30% · EMA 25% · RSI 25% · MACD 20%"
    print(f"")
    print(f"{weights_str}")

    if comp:
        print(f"\n{'─'*62}")
        print(f"  COMPOSITE SCORE  : {comp['score']} / 10")
        print(f"  RECOMMENDATION   : {comp['recommendation']}")
        print(f"  OVERALL TREND    : {comp['trend']}")
    print(f"{'='*62}\n")


def print_comparison(results):
    print(f"\n{'='*62}")
    print(f"  CROSS-MODULE COMPARISON SUMMARY")
    print(f"{'='*62}")
    print(f"  {'Symbol':<8} {'MACD':>5} {'RSI':>5} {'SMA':>5} {'EMA':>5} {'Composite':>9}  {'Recommendation'}")
    print(f"  {'─'*58}")
    for r in sorted(results, key=lambda x: (x.get("composite") or {}).get("score") or 0, reverse=True):
        s = r.get("scores", {})
        comp = (r.get("composite") or {}).get("score")
        rec  = (r.get("composite") or {}).get("recommendation", "N/A")
        macd = f"{s.get('macd'):.1f}" if s.get("macd") else " N/A"
        rsi  = f"{s.get('rsi'):.1f}"  if s.get("rsi")  else " N/A"
        sma  = f"{s.get('sma'):.1f}"  if s.get("sma")  else " N/A"
        ema  = f"{s.get('ema'):.1f}"  if s.get("ema")  else " N/A"
        comp_str = f"{comp:.1f}" if comp else " N/A"
        print(f"  {r['symbol']:<8} {macd:>5} {rsi:>5} {sma:>5} {ema:>5} {comp_str:>9}  {rec}")
    print(f"{'='*62}\n")


def main():
    args = sys.argv[1:]

    if args:
        raw_symbols = args[0]
    else:
        raw_symbols = input("Enter stock symbol(s), comma-separated (e.g. AAPL, TSLA): ").strip()

    symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: No symbols provided.")
        return

    today      = datetime.today().strftime("%Y-%m-%d")
    two_yr_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    results = []
    for symbol in symbols:
        print(f"\nAnalysing {symbol}...")
        try:
            details = fetch_ticker_details(symbol)
            print(f"  {details['name']}  |  {details['cap_tier']}")
            prices, volumes = fetch_daily_prices(symbol, two_yr_ago, today)
            print(f"  Price history: {len(prices)} days")
        except ValueError as e:
            print(f"  ERROR: {e}")
            continue

        result = analyze(symbol, prices, volumes, details)
        results.append(result)

    for r in results:
        print_result(r)

    if len(results) > 1:
        print_comparison(results)


if __name__ == "__main__":
    main()
