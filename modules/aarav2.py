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

# Market cap thresholds (USD)
CAP_TIERS = [
    (200_000_000_000, "Mega Cap",  "Top-tier blue chips (>$200B)"),
    ( 10_000_000_000, "Large Cap", "$10B–$200B"),
    (  2_000_000_000, "Mid Cap",   "$2B–$10B"),
    (              0, "Small Cap", "<$2B"),
]


def classify_cap(market_cap):
    if market_cap is None:
        return "Unknown", "Market cap unavailable"
    for threshold, label, desc in CAP_TIERS:
        if market_cap >= threshold:
            return label, desc
    return "Small Cap", "<$2B"


def fetch_ticker_details(symbol):
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
    """Returns (prices, volumes) — both dicts keyed by date string."""
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        raise ValueError(f"Could not fetch data for {symbol}: {data.get('message') or data.get('status')}")
    prices, volumes = {}, {}
    for bar in data["results"]:
        date = datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
        prices[date]  = bar["c"]
        volumes[date] = bar.get("v", 0)
    return prices, volumes


def fetch_rsi_series(symbol, window=14, limit=500):
    """
    Fetch a full historical RSI series (up to `limit` daily readings).
    Returns a list of {"timestamp": ms, "value": float} dicts, newest first.
    limit=500 covers ~2 years of trading days.
    """
    import time
    url = f"{BASE_URL}/v1/indicators/rsi/{symbol}"
    params = {
        "timespan":    "day",
        "adjusted":    "true",
        "window":      window,
        "series_type": "close",
        "order":       "desc",
        "limit":       limit,
        "apiKey":      API_KEY,
    }
    for attempt in range(5):
        if attempt:
            time.sleep(3.0 * attempt)
        r = requests.get(url, params=params)
        if r.status_code == 429:
            continue
        data = r.json()
        if data.get("status") not in ("OK", "DELAYED"):
            raise ValueError(f"RSI fetch failed for {symbol}: {data.get('message') or data.get('status')}")
        return data.get("results", {}).get("values", [])
    raise ValueError(f"RSI fetch rate-limited for {symbol} after 5 attempts")


def compute_context(prices_dict):
    dates = sorted(prices_dict.keys())
    closes = [prices_dict[d] for d in dates]
    current = closes[-1]
    ma200 = sum(closes[-200:]) / min(200, len(closes))
    week52_high = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    return current, ma200, week52_high


def compute_volume_profile(volumes_dict):
    """Compare recent 20-day avg volume to 90-day baseline."""
    dates = sorted(volumes_dict.keys())
    if len(dates) < 20:
        return {"avg_20d": None, "avg_90d": None, "ratio": None, "confidence": "Normal"}

    vols = [volumes_dict[d] for d in dates]
    avg_20d = sum(vols[-20:]) / 20
    baseline = vols[-90:] if len(vols) >= 90 else vols
    avg_90d = sum(baseline) / len(baseline)
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


def _linear_slope(values):
    """Least-squares slope (RSI points per day)."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def _swing_lows(values, lookback=4):
    """Indices of local minima."""
    idx = []
    for i in range(lookback, len(values) - lookback):
        window = values[i - lookback: i + lookback + 1]
        if values[i] == min(window):
            idx.append(i)
    return idx


def _swing_highs(values, lookback=4):
    """Indices of local maxima."""
    idx = []
    for i in range(lookback, len(values) - lookback):
        window = values[i - lookback: i + lookback + 1]
        if values[i] == max(window):
            idx.append(i)
    return idx


def analyze_rsi_series(rsi_series, prices_dict=None):
    """
    Full trader-grade RSI analysis using 4 weighted components + failure swing bonus.

    Components and weights:
      35%  Percentile rank  — where current RSI sits in the stock's own 2-year history
      30%  Divergence       — price/RSI divergence over recent swing points
      20%  Centerline regime — consecutive days above/below 50
      15%  Momentum/slope   — 5-day and 14-day RSI linear regression slope
      ±1   Failure swing     — confirmed reversal pattern bonus

    Returns a dict ready to drop into analyze() → composite scoring.
    """
    if not rsi_series:
        return None

    # rsi_series arrives newest-first; reverse to chronological
    chrono     = list(reversed(rsi_series))
    rsi_values = [e["value"] for e in chrono]
    rsi_dates  = [
        datetime.fromtimestamp(e["timestamp"] / 1000).strftime("%Y-%m-%d")
        for e in chrono
    ]
    n       = len(rsi_values)
    current = round(rsi_values[-1], 2)

    # ── 1. PERCENTILE RANK (35%) ─────────────────────────────────────────────
    below   = sum(1 for v in rsi_values if v < current)
    percentile = round(below / n * 100, 1)

    if percentile <= 10:   level_score = 10.0
    elif percentile <= 20: level_score = 9.0
    elif percentile <= 30: level_score = 8.0
    elif percentile <= 40: level_score = 7.0
    elif percentile <= 50: level_score = 6.0
    elif percentile <= 60: level_score = 5.0
    elif percentile <= 70: level_score = 4.0
    elif percentile <= 80: level_score = 3.0
    elif percentile <= 90: level_score = 2.0
    else:                  level_score = 1.0

    # ── 2. DIVERGENCE (30%) ──────────────────────────────────────────────────
    divergence       = "None"
    divergence_score = 5.0  # neutral if undetected

    if prices_dict and n >= 20:
        aligned = [prices_dict.get(d) for d in rsi_dates]
        lookback_bars = min(60, n)
        rsi_w   = rsi_values[-lookback_bars:]
        price_w = aligned[-lookback_bars:]

        paired = [(p, r) for p, r in zip(price_w, rsi_w) if p is not None]
        if len(paired) >= 20:
            px = [x[0] for x in paired]
            rx = [x[1] for x in paired]
            lows  = _swing_lows(px)
            highs = _swing_highs(px)

            # Bullish: price lower low + RSI higher low
            if len(lows) >= 2:
                i1, i2 = lows[-2], lows[-1]
                if px[i2] < px[i1] and rx[i2] > rx[i1]:
                    divergence       = "Bullish"
                    divergence_score = 8.5

            # Bearish: price higher high + RSI lower high (overrides bullish)
            if len(highs) >= 2:
                i1, i2 = highs[-2], highs[-1]
                if px[i2] > px[i1] and rx[i2] < rx[i1]:
                    divergence       = "Bearish"
                    divergence_score = 1.5

    # ── 3. CENTERLINE REGIME (20%) ───────────────────────────────────────────
    above_50 = rsi_values[-1] >= 50
    streak   = 0
    for v in reversed(rsi_values):
        if (v >= 50) == above_50:
            streak += 1
        else:
            break

    if above_50:
        if streak >= 60:   regime_score = 8.0
        elif streak >= 30: regime_score = 7.0
        elif streak >= 10: regime_score = 6.0
        else:              regime_score = 5.5
    else:
        if streak >= 60:   regime_score = 2.0
        elif streak >= 30: regime_score = 3.0
        elif streak >= 10: regime_score = 4.0
        else:              regime_score = 4.5

    # ── 4. MOMENTUM / SLOPE (15%) ────────────────────────────────────────────
    slope_5  = _linear_slope(rsi_values[-5:])  if n >= 5  else 0.0
    slope_14 = _linear_slope(rsi_values[-14:]) if n >= 14 else 0.0
    combined = slope_5 * 0.6 + slope_14 * 0.4  # recency-weighted

    if combined >= 1.5:    momentum_score = 8.0
    elif combined >= 0.5:  momentum_score = 6.5
    elif combined >= 0.0:  momentum_score = 5.5
    elif combined >= -0.5: momentum_score = 4.5
    elif combined >= -1.5: momentum_score = 3.5
    else:                  momentum_score = 2.0

    # ── 5. FAILURE SWING BONUS (±1) ──────────────────────────────────────────
    failure_swing = "None"
    failure_bonus = 0.0
    recent20 = rsi_values[-20:] if n >= 20 else rsi_values

    # Bullish failure swing: dipped below 30, bounced, pulled back (stayed >30), broke above bounce high
    below30 = [i for i, v in enumerate(recent20) if v < 30]
    if below30:
        tail = recent20[below30[-1]:]
        if len(tail) >= 4:
            bounce_high  = max(tail)
            pullback_low = min(tail[1:])
            if pullback_low > 30 and tail[-1] >= bounce_high * 0.98:
                failure_swing = "Bullish Failure Swing"
                failure_bonus = 1.0

    # Bearish failure swing: exceeded 70, dropped, rallied (stayed <70), broke below pullback low
    above70 = [i for i, v in enumerate(recent20) if v > 70]
    if above70 and failure_swing == "None":
        tail = recent20[above70[-1]:]
        if len(tail) >= 4:
            pullback_low = min(tail)
            rally_high   = max(tail[1:])
            if rally_high < 70 and tail[-1] <= pullback_low * 1.02:
                failure_swing = "Bearish Failure Swing"
                failure_bonus = -1.0

    # ── WEIGHTED COMPOSITE ───────────────────────────────────────────────────
    raw = (
        level_score      * 0.35 +
        divergence_score * 0.30 +
        regime_score     * 0.20 +
        momentum_score   * 0.15
    ) + failure_bonus
    score = round(max(1.0, min(10.0, raw)), 2)

    # Absolute RSI label (traders think in absolute terms)
    if current <= 30:   label = "Oversold (Bullish)"
    elif current <= 45: label = "Approaching Oversold"
    elif current <= 55: label = "Neutral"
    elif current <= 70: label = "Approaching Overbought"
    else:               label = "Overbought (Bearish)"

    avg_rsi        = round(sum(rsi_values) / n, 2)
    pct_oversold   = round(sum(1 for v in rsi_values if v <= 30) / n * 100, 1)
    pct_overbought = round(sum(1 for v in rsi_values if v >= 70) / n * 100, 1)

    return {
        "current":        current,
        "score":          score,
        "label":          label,
        "percentile":     percentile,
        "avg_rsi":        avg_rsi,
        "pct_oversold":   pct_oversold,
        "pct_overbought": pct_overbought,
        "regime":         "Bullish" if above_50 else "Bearish",
        "regime_days":    streak,
        "slope_5d":       round(slope_5, 3),
        "slope_14d":      round(slope_14, 3),
        "divergence":     divergence,
        "failure_swing":  failure_swing,
        "readings":       n,
        "components": {
            "level":         round(level_score, 2),
            "divergence":    round(divergence_score, 2),
            "regime":        round(regime_score, 2),
            "momentum":      round(momentum_score, 2),
            "failure_bonus": failure_bonus,
        },
    }


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


def analyze(symbol, all_prices, all_volumes=None, ticker_details=None, rsi_series=None):
    current_price, ma200, week52_high = compute_context(all_prices)
    pct_from_high = (week52_high - current_price) / week52_high * 100

    vol_profile = compute_volume_profile(all_volumes) if all_volumes else {
        "avg_20d": None, "avg_90d": None, "ratio": None, "confidence": "Normal"
    }

    rsi_data = analyze_rsi_series(rsi_series, prices_dict=all_prices) if rsi_series else None
    td = ticker_details or {}

    composite_score = round(rsi_data["score"], 1) if rsi_data else None
    # MA200 floor: don't label a pullback in an uptrend as a sell
    if composite_score is not None and current_price > ma200:
        composite_score = max(composite_score, 4.5)

    return {
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
        "rsi": rsi_data,
        "volume": vol_profile,
        "composite": {
            "score":          composite_score,
            "trend":          trend_label(round(composite_score)) if composite_score else "N/A",
            "recommendation": buy_label(round(composite_score)) if composite_score else "N/A",
        } if composite_score is not None else None,
    }


def print_result(result):
    ctx = result["context"]
    cap = result.get("cap", {})
    vol = result.get("volume", {})
    rsi = result.get("rsi") or {}
    above = "ABOVE ✓" if ctx["above_ma200"] else "BELOW ✗"

    mcap = cap.get("market_cap")
    mcap_str = f"${mcap/1e9:.1f}B" if mcap and mcap >= 1e9 else (f"${mcap/1e6:.0f}M" if mcap else "N/A")

    ratio = vol.get("ratio")
    vol_str = f"{vol.get('confidence', 'N/A')}  (20d/90d ratio: {ratio:.2f})" if ratio else vol.get("confidence", "N/A")

    print(f"\n{'='*62}")
    print(f"  Stock Analysis — {result['symbol']}  ({result.get('company', '')})")
    print(f"{'='*62}")
    print(f"  Cap Tier     : {cap.get('tier', 'Unknown')}  ({cap.get('description', '')})")
    print(f"  Market Cap   : {mcap_str}")
    print(f"  Price        : ${ctx['price']:.2f}")
    print(f"  200-day MA   : ${ctx['ma200']:.2f}  ({above})")
    print(f"  52-Week High : ${ctx['week52_high']:.2f}  ({ctx['pct_from_high']:.1f}% below high)")
    print(f"  Volume       : {vol_str}")
    print(f"")
    if rsi.get("current") is not None:
        c = rsi["components"]
        div   = rsi["divergence"]
        fsw   = rsi["failure_swing"]
        div_str = f"  ◆ {div} Divergence detected" if div != "None" else ""
        fsw_str = f"  ◆ {fsw}" if fsw != "None" else ""

        print(f"  RSI (14)     : {rsi['current']}  →  {rsi['label']}")
        print(f"  Percentile   : {rsi['percentile']}th  (vs own 2yr history;  avg RSI: {rsi['avg_rsi']})")
        print(f"  Regime       : {rsi['regime']}  ({rsi['regime_days']} consecutive days above/below 50)")
        print(f"  Momentum     : slope 5d={rsi['slope_5d']:+.3f}  14d={rsi['slope_14d']:+.3f}  (RSI pts/day)")
        print(f"  History      : Oversold {rsi['pct_oversold']}%  /  Overbought {rsi['pct_overbought']}%  "
              f"of {rsi['readings']} days")
        if div_str: print(f"{div_str}")
        if fsw_str: print(f"{fsw_str}")
        bonus_str = f"  bonus:{c['failure_bonus']:+g}" if c['failure_bonus'] else ""
        print(f"  Score        : {rsi['score']}/10  "
              f"[level:{c['level']}×35%  div:{c['divergence']}×30%  "
              f"regime:{c['regime']}×20%  mom:{c['momentum']}×15%{bonus_str}]")
    else:
        print(f"  RSI (14)     : N/A")

    if result.get("composite"):
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
    else:
        raw_symbols = input("Enter stock symbol(s), comma-separated (e.g. AAPL, TSLA, V): ").strip()

    symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: No symbols provided.")
        return

    today = datetime.today().strftime("%Y-%m-%d")
    two_years_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    results = []
    for symbol in symbols:
        print(f"\nFetching data for {symbol}...")
        try:
            details = fetch_ticker_details(symbol)
            print(f"  {details['name']}  |  {details['cap_tier']}  ({details['cap_desc']})")
            prices, volumes = fetch_daily_prices(symbol, two_years_ago, today)
        except ValueError as e:
            print(f"  ERROR: {e}")
            continue

        rsi_series = fetch_rsi_series(symbol)
        print(f"  RSI history  : {len(rsi_series)} readings fetched")
        print(f"  Price history: {len(prices)} trading days")

        result = analyze(symbol, prices, volumes, details, rsi_series=rsi_series)
        results.append(result)

    for result in results:
        print_result(result)

    if len(results) > 1:
        print_comparison(results)


def print_comparison(results):
    print(f"\n{'='*62}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*62}")
    print(f"  {'Symbol':<8} {'Cap Tier':<12} {'RSI':>6}  {'Score':>5}  {'Recommendation':<22} {'Vol'}")
    print(f"  {'─'*58}")

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
            rsi_v = r["rsi"].get("current", "N/A") if r.get("rsi") else "N/A"
            vol_c = r["volume"].get("confidence", "N/A")
            rank  = sorted(tier_scores, reverse=True).index(score) + 1 if tier_scores else "-"
            rel   = f"#{rank}/{len(tier_scores)} in {tier}"
            print(f"  {r['symbol']:<8} {tier:<12} {str(rsi_v):>6}  {score:>5.1f}  {rec:<22} {vol_c}  ({rel})")

    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
