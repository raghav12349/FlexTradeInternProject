import os
import sys
import requests
from datetime import datetime, timedelta

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

API_KEY = _load_api_key()
BASE_URL = "https://api.polygon.io"

# Valid timeframe keys the user/UI can pass in
TIMEFRAMES = {
    "1M":  ("1 Month",   30),
    "3M":  ("3 Months",  90),
    "6M":  ("6 Months",  180),
    "1Y":  ("1 Year",    365),
    "2Y":  ("2 Years",   730),
}

# Market cap thresholds (USD)
CAP_TIERS = [
    (200_000_000_000, "Mega Cap",  "Top-tier blue chips (>$200B)"),
    ( 10_000_000_000, "Large Cap", "$10B–$200B"),
    (  2_000_000_000, "Mid Cap",   "$2B–$10B"),
    (              0, "Small Cap", "<$2B"),
]


def classify_cap(market_cap):
    """Return (tier_label, description) for a given market cap in USD."""
    if market_cap is None:
        return "Unknown", "Market cap unavailable"
    for threshold, label, desc in CAP_TIERS:
        if market_cap >= threshold:
            return label, desc
    return "Small Cap", "<$2B"


def fetch_ticker_details(symbol):
    """
    Fetch company name and market cap from Polygon ticker details.
    Returns dict with keys: name, market_cap, cap_tier, cap_desc
    """
    url = f"{BASE_URL}/v3/reference/tickers/{symbol}"
    r = requests.get(url, params={"apiKey": API_KEY})
    data = r.json()
    results = data.get("results", {})
    market_cap = results.get("market_cap")
    cap_tier, cap_desc = classify_cap(market_cap)
    return {
        "name":       results.get("name", symbol),
        "market_cap": market_cap,
        "cap_tier":   cap_tier,
        "cap_desc":   cap_desc,
    }


def fetch_daily_prices(symbol, start_date, end_date):
    """
    Returns (prices, volumes) — both dicts keyed by date string.
    prices:  {date: close_price}
    volumes: {date: daily_volume}
    """
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        raise ValueError(f"Could not fetch data for {symbol}: {data.get('message') or data.get('status')}")
    prices, volumes = {}, {}
    for bar in data["results"]:
        date = datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
        prices[date]  = bar["c"]
        volumes[date] = bar.get("v", 0)
    return prices, volumes


def ema(values, period):
    """EMA with SMA seed for first `period` bars — eliminates cold-start price bias."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result  # length = len(values) - period + 1


def _linear_slope(values):
    """Least-squares slope."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def compute_macd(prices_dict):
    """
    Compute MACD on the FULL price history.
    Returns a dict with current values + dated series for timeframe slicing.
    """
    dates = sorted(prices_dict.keys())
    closes = [prices_dict[d] for d in dates]
    if len(closes) < 35:
        return None

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    if not ema12 or not ema26:
        return None
    # ema12 is 14 bars longer; drop the lead to align
    ema12_a = ema12[14:]
    macd_line = [e12 - e26 for e12, e26 in zip(ema12_a, ema26)]
    if len(macd_line) < 9:
        return None
    signal_line = ema(macd_line, 9)
    if not signal_line:
        return None
    # signal_line[i] ↔ macd_line[i+8]
    macd_a = macd_line[8:]
    histogram = [m - s for m, s in zip(macd_a, signal_line)]
    # date alignment: ema26 consumes 25 bars, signal consumes 8 more → offset 33
    aligned_dates = dates[33:]
    return {
        "macd":       macd_a[-1],
        "signal":     signal_line[-1],
        "histogram":  histogram[-1],
        "macd_line":  macd_a,
        "sig_line":   signal_line,
        "hist_series": histogram,
        "dates":      aligned_dates,
    }


def compute_context(prices_dict):
    dates = sorted(prices_dict.keys())
    closes = [prices_dict[d] for d in dates]
    current = closes[-1]
    ma200 = sum(closes[-200:]) / min(200, len(closes))
    week52_high = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    return current, ma200, week52_high


def compute_volume_profile(volumes_dict, start_date, end_date):
    """
    Compares recent 20-day avg volume to the 90-day avg over the window.
    Returns a dict with avg volumes and a confidence label used to adjust scoring.

    confidence:
      "High"   — recent volume expanding vs baseline (signals more reliable)
      "Normal" — volume in line with baseline
      "Low"    — recent volume contracting (signals less reliable, fade moves)
    """
    dates = sorted(d for d in volumes_dict if start_date <= d <= end_date)
    if len(dates) < 20:
        return {"avg_20d": None, "avg_90d": None, "confidence": "Normal", "ratio": None}

    vols = [volumes_dict[d] for d in dates]
    avg_20d = sum(vols[-20:]) / 20
    baseline_window = vols[-90:] if len(vols) >= 90 else vols
    avg_90d = sum(baseline_window) / len(baseline_window)

    ratio = avg_20d / avg_90d if avg_90d else 1.0

    if ratio >= 1.20:
        confidence = "High"
    elif ratio <= 0.80:
        confidence = "Low"
    else:
        confidence = "Normal"

    return {
        "avg_20d":    round(avg_20d),
        "avg_90d":    round(avg_90d),
        "ratio":      round(ratio, 2),
        "confidence": confidence,
    }


def score_macd(macd_full, start_date, volume_confidence="Normal"):
    """
    Score MACD for a specific timeframe window.

    Uses histogram linear-regression slope over the window (not just last 3 bars)
    so different timeframes produce genuinely different scores.

    Weights:
      30%  MACD vs Signal line (trend direction)
      35%  Histogram slope over window (momentum acceleration/deceleration)
      15%  Histogram sign (current momentum direction)
      10%  MACD zero line (absolute trend)
      10%  Zero-line cross (recent reversal signal)
    """
    macd   = macd_full["macd"]
    signal = macd_full["signal"]
    hist   = macd_full["histogram"]

    # Slice histogram and MACD to this timeframe window
    window_hist = [h for d, h in zip(macd_full["dates"], macd_full["hist_series"]) if d >= start_date]
    window_macd = [m for d, m in zip(macd_full["dates"], macd_full["macd_line"]) if d >= start_date]

    score = 5.0

    # 1. MACD vs Signal (30%)
    score += 2.0 if macd > signal else -2.0

    # 2. Histogram slope over the timeframe window (35%)
    if len(window_hist) >= 5:
        slope = _linear_slope(window_hist)
        # Normalize by full histogram range so short/long windows are comparable
        hist_range = max(macd_full["hist_series"]) - min(macd_full["hist_series"])
        norm = slope / hist_range * 100 if hist_range > 0.001 else 0
        if norm > 5:    score += 2.5
        elif norm > 1:  score += 1.5
        elif norm > 0:  score += 0.5
        elif norm > -1: score -= 0.5
        elif norm > -5: score -= 1.5
        else:           score -= 2.5

    # 3. Histogram sign (15%)
    score += 0.75 if hist > 0 else -0.75

    # 4. MACD zero line (10%)
    score += 0.5 if macd > 0 else -0.5

    # 5. Zero-line cross within window — fresh cross = stronger signal (10%)
    if len(window_macd) >= 3:
        if window_macd[-2] <= 0 < window_macd[-1]:
            score += 1.0
        elif window_macd[-2] >= 0 > window_macd[-1]:
            score -= 1.0

    # Volume confidence: expanding confirms, contracting dampens
    if volume_confidence == "High":
        score += 0.5 if score > 5 else -0.5
    elif volume_confidence == "Low":
        score += 1.0 if score < 5 else -1.0

    return max(1, min(10, round(score, 1)))


def trend_label(score):
    if score >= 8: return "Strong Uptrend (Bullish)"
    if score >= 6: return "Moderate Uptrend"
    if score >= 5: return "Sideways / Neutral"
    if score >= 3: return "Moderate Downtrend"
    return "Strong Downtrend (Bearish)"


def buy_label(score):
    if score >= 8: return "Strong Buy"
    if score >= 6: return "Buy"
    if score >= 5: return "Neutral / Hold"
    if score >= 3: return "Sell / Avoid"
    return "Strong Sell"


def analyze(symbol, all_prices, all_volumes=None, ticker_details=None, timeframe="ALL"):
    """
    Core analysis function — web-ready.

    Parameters:
        symbol         : stock ticker string
        all_prices     : dict of {date_str: close_price}
        all_volumes    : dict of {date_str: daily_volume}  (optional)
        ticker_details : dict from fetch_ticker_details()  (optional)
        timeframe      : one of "1M", "3M", "6M", "1Y", "2Y", or "ALL"

    Returns a dict with keys: symbol, company, cap, context, timeframes, composite
    """
    timeframe = timeframe.upper()
    if timeframe not in TIMEFRAMES and timeframe != "ALL":
        raise ValueError(f"Invalid timeframe '{timeframe}'. Choose from: {', '.join(TIMEFRAMES)} or ALL")

    selected = list(TIMEFRAMES.items()) if timeframe == "ALL" else [(timeframe, TIMEFRAMES[timeframe])]

    today = datetime.today().strftime("%Y-%m-%d")
    two_years_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    current_price, ma200, week52_high = compute_context(all_prices)
    pct_from_high = (week52_high - current_price) / week52_high * 100

    td = ticker_details or {}
    result = {
        "symbol":  symbol.upper(),
        "company": td.get("name", symbol.upper()),
        "cap": {
            "tier":        td.get("cap_tier", "Unknown"),
            "description": td.get("cap_desc", ""),
            "market_cap":  td.get("market_cap"),
        },
        "context": {
            "price":         round(current_price, 2),
            "ma200":         round(ma200, 2),
            "above_ma200":   current_price > ma200,
            "week52_high":   round(week52_high, 2),
            "pct_from_high": round(pct_from_high, 2),
        },
        "timeframes": [],
        "composite":  None,
    }

    # Compute MACD once on full history; timeframes only change the scoring window
    macd_full = compute_macd(all_prices)
    if macd_full is None:
        return result  # not enough data at all

    scores = []
    for tf_key, (label, days) in selected:
        start = max(
            (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d"),
            two_years_ago,
        )
        # Check at least 5 MACD histogram bars exist in this window
        window_count = sum(1 for d in macd_full["dates"] if d >= start)
        if window_count < 5:
            result["timeframes"].append({"key": tf_key, "label": label, "error": "Not enough data"})
            continue

        vol_profile = (
            compute_volume_profile(all_volumes, start, today)
            if all_volumes else
            {"avg_20d": None, "avg_90d": None, "ratio": None, "confidence": "Normal"}
        )

        score = score_macd(macd_full, start, vol_profile["confidence"])
        scores.append(score)

        result["timeframes"].append({
            "key":       tf_key,
            "label":     label,
            "macd":      round(macd_full["macd"], 4),
            "signal":    round(macd_full["signal"], 4),
            "histogram": round(macd_full["histogram"], 4),
            "score":     score,
            "trend":     trend_label(score),
            "recommendation": buy_label(score),
            "volume": {
                "avg_20d":    vol_profile["avg_20d"],
                "avg_90d":    vol_profile["avg_90d"],
                "ratio":      vol_profile["ratio"],
                "confidence": vol_profile["confidence"],
            },
        })

    if scores:
        avg = sum(scores) / len(scores)
        avg_score = round(avg)
        result["composite"] = {
            "score":          round(avg, 1),
            "trend":          trend_label(avg_score),
            "recommendation": buy_label(avg_score),
        }

    return result


def print_result(result):
    ctx = result["context"]
    cap = result.get("cap", {})
    above = "ABOVE ✓" if ctx["above_ma200"] else "BELOW ✗"

    mcap = cap.get("market_cap")
    mcap_str = f"${mcap/1e9:.1f}B" if mcap and mcap >= 1e9 else (f"${mcap/1e6:.0f}M" if mcap else "N/A")

    print(f"\n{'='*62}")
    print(f"  MACD Analysis — {result['symbol']}  ({result.get('company', '')})")
    print(f"{'='*62}")
    print(f"  Cap Tier     : {cap.get('tier', 'Unknown')}  ({cap.get('description', '')})")
    print(f"  Market Cap   : {mcap_str}")
    print(f"  Price        : ${ctx['price']:.2f}")
    print(f"  200-day MA   : ${ctx['ma200']:.2f}  ({above})")
    print(f"  52-Week High : ${ctx['week52_high']:.2f}  ({ctx['pct_from_high']:.1f}% below high)")

    for tf in result["timeframes"]:
        if "error" in tf:
            print(f"\n  [{tf['label']}]  {tf['error']}")
            continue
        vol = tf.get("volume", {})
        conf = vol.get("confidence", "Normal")
        ratio = vol.get("ratio")
        vol_str = f"{conf}  (20d/90d vol ratio: {ratio:.2f})" if ratio else conf

        print(f"\n  Timeframe  : {tf['label']}")
        print(f"  MACD       : {tf['macd']:+.4f}")
        print(f"  Signal     : {tf['signal']:+.4f}")
        print(f"  Histogram  : {tf['histogram']:+.4f}")
        print(f"  Volume     : {vol_str}")
        print(f"  Score      : {tf['score']}/10")
        print(f"  Trend      : {tf['trend']}")
        print(f"  Signal     : {tf['recommendation']}")

    if result["composite"]:
        c = result["composite"]
        print(f"\n{'─'*62}")
        print(f"  COMPOSITE SCORE  : {c['score']} / 10")
        print(f"  RECOMMENDATION   : {c['recommendation']}")
        print(f"  OVERALL TREND    : {c['trend']}")
    print(f"{'='*62}\n")


def main():
    args = sys.argv[1:]

    if args:
        raw_symbols = args[0]
        timeframe = args[1].upper() if len(args) > 1 else "ALL"
    else:
        raw_symbols = input("Enter stock symbol(s), comma-separated (e.g. AAPL, TSLA, V): ").strip()
        tf_options = ", ".join(TIMEFRAMES.keys())
        timeframe = input(f"Enter timeframe ({tf_options}, or ALL) [default ALL]: ").strip().upper() or "ALL"

    symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: No symbols provided.")
        return

    today = datetime.today().strftime("%Y-%m-%d")
    two_years_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    results = []
    for symbol in symbols:
        print(f"\nFetching ticker details and 2 years of data for {symbol}...")
        try:
            details = fetch_ticker_details(symbol)
            print(f"  {details['name']}  |  {details['cap_tier']}  ({details['cap_desc']})")
            prices, volumes = fetch_daily_prices(symbol, two_years_ago, today)
        except ValueError as e:
            print(f"  ERROR: {e}")
            continue

        print(f"  Retrieved {len(prices)} trading days.")

        try:
            result = analyze(symbol, prices, volumes, details, timeframe)
            results.append(result)
        except ValueError as e:
            print(f"  ERROR: {e}")
            continue

    for result in results:
        print_result(result)

    if len(results) > 1:
        print_comparison(results)


def print_comparison(results):
    """Print a side-by-side summary table when multiple symbols are analysed."""
    print(f"\n{'='*62}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*62}")
    print(f"  {'Symbol':<8} {'Cap Tier':<12} {'Score':>5}  {'Recommendation':<22} {'Vol Confidence'}")
    print(f"  {'─'*58}")

    # Group by cap tier for relative context
    by_tier = {}
    for r in results:
        tier = r["cap"]["tier"]
        by_tier.setdefault(tier, []).append(r)

    for tier, tier_results in sorted(by_tier.items()):
        tier_scores = [r["composite"]["score"] for r in tier_results if r.get("composite")]
        for r in tier_results:
            comp = r.get("composite")
            if not comp:
                continue
            score = comp["score"]
            rec   = comp["recommendation"]
            # Relative rank within same cap tier
            rank  = sorted(tier_scores, reverse=True).index(score) + 1 if tier_scores else "-"
            # Last timeframe's volume confidence as a proxy
            last_vol = next(
                (tf["volume"]["confidence"] for tf in reversed(r["timeframes"]) if "volume" in tf),
                "N/A"
            )
            rel = f"#{rank}/{len(tier_scores)} in {tier}"
            print(f"  {r['symbol']:<8} {tier:<12} {score:>5.1f}  {rec:<22} {last_vol}  ({rel})")

    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
