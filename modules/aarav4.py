import os
import sys
from datetime import datetime, timedelta

# The data layer is the `massive` RESTClient (drop-in compatible with the
# polygon client — identical get_ema / get_aggs / get_ticker_details surface).
try:
    from massive import RESTClient
except ImportError:
    from polygon import RESTClient

API_KEY = os.environ.get("POLYGON_API_KEY")
if not API_KEY:
    raise RuntimeError("POLYGON_API_KEY environment variable is not set")
client = RESTClient(API_KEY)

# EMA windows we stack to read trend structure. EMA reacts faster than SMA, so
# the same 20/50/200 ladder gives an earlier read on the same trend questions.
EMA_WINDOWS = (20, 50, 200)

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
    Fetch company name and market cap via the RESTClient.
    Returns dict with keys: name, market_cap, cap_tier, cap_desc
    """
    try:
        d = client.get_ticker_details(symbol)
        name = getattr(d, "name", symbol)
        market_cap = getattr(d, "market_cap", None)
    except Exception:
        name, market_cap = symbol, None
    cap_tier, cap_desc = classify_cap(market_cap)
    return {
        "name":       name,
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
    bars = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="day",
        from_=start_date,
        to=end_date,
        adjusted="true",
        sort="asc",
        limit=50000,
    )
    if not bars:
        raise ValueError(f"Could not fetch price data for {symbol}")
    prices, volumes = {}, {}
    for bar in bars:
        date = datetime.utcfromtimestamp(bar.timestamp / 1000).strftime("%Y-%m-%d")
        prices[date]  = bar.close
        volumes[date] = getattr(bar, "volume", 0) or 0
    return prices, volumes


def fetch_ema_series(symbol, window, limit=260):
    """
    Fetch a historical EMA series for one window via client.get_ema().
    Returns a list of {"timestamp": ms, "value": float} dicts, oldest first.
    limit=260 covers ~1 trading year — enough to detect 50/200 crossovers.
    """
    ema = client.get_ema(
        ticker=symbol,
        timespan="day",
        adjusted="true",
        window=str(window),
        series_type="close",
        order="desc",
        limit=str(limit),
    )
    values = getattr(ema, "values", None) or []
    series = [{"timestamp": v.timestamp, "value": v.value} for v in values]
    series.reverse()  # API returns newest-first; flip to chronological
    return series


def compute_context(prices_dict):
    dates = sorted(prices_dict.keys())
    closes = [prices_dict[d] for d in dates]
    current = closes[-1]
    ma200 = sum(closes[-200:]) / min(200, len(closes))
    week52_high = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    return current, ma200, week52_high


def compute_volume_profile(volumes_dict):
    """Compare recent 20-day avg volume to a 90-day baseline."""
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
    """Least-squares slope (price units per day)."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def analyze_ema(ema_map, current_price, volume_confidence="Normal"):
    """
    Trader-grade EMA analysis using 4 weighted components.

    ema_map: {window: [{"timestamp", "value"}, ...]}  (chronological per window)

    Components and weights:
      35%  Stack alignment — price vs EMA20 vs EMA50 vs EMA200 ordering
      25%  Crossover       — recent golden / death cross of EMA50 over EMA200
      20%  Slope           — EMA50 trend direction & strength
      20%  Extension       — how stretched price is from EMA20 (mean reversion)

    EMA weights recent prices more heavily than SMA, so the stack and crossover
    reads here turn earlier than the SMA equivalent — earlier signal, but also
    more prone to whipsaw, which the volume-confidence adjustment helps temper.

    Returns a dict ready to drop into analyze() → composite scoring.
    """
    latest = {}
    for w in EMA_WINDOWS:
        series = ema_map.get(w) or []
        latest[w] = series[-1]["value"] if series else None

    ema20, ema50, ema200 = latest.get(20), latest.get(50), latest.get(200)
    if ema20 is None or ema50 is None:
        return None

    # ── 1. STACK ALIGNMENT (35%) ─────────────────────────────────────────────
    stack = [current_price, ema20, ema50]
    if ema200 is not None:
        stack.append(ema200)

    bullish_stack = all(stack[i] > stack[i + 1] for i in range(len(stack) - 1))
    bearish_stack = all(stack[i] < stack[i + 1] for i in range(len(stack) - 1))

    if bullish_stack:
        stack_score, stack_label = 9.0, "Bullish stack (price > 20 > 50 > 200)"
    elif bearish_stack:
        stack_score, stack_label = 1.5, "Bearish stack (price < 20 < 50 < 200)"
    else:
        # Partial credit: count how many "price above EMA" conditions hold
        above = sum(1 for s in (ema20, ema50, ema200) if s is not None and current_price > s)
        total = sum(1 for s in (ema20, ema50, ema200) if s is not None)
        stack_score = 3.5 + (above / total) * 3.0 if total else 5.0
        stack_label = f"Mixed ({above}/{total} EMAs below price)"

    # ── 2. CROSSOVER (25%) ───────────────────────────────────────────────────
    cross = "None"
    cross_score = 5.0
    e50 = ema_map.get(50) or []
    e200 = ema_map.get(200) or []
    if len(e50) >= 6 and len(e200) >= 6:
        n = min(len(e50), len(e200))
        f = [e50[-(n - i)]["value"] for i in range(n)]    # fast (50)
        s = [e200[-(n - i)]["value"] for i in range(n)]   # slow (200)
        diff = [a - b for a, b in zip(f, s)]
        # Look back ~10 bars for a sign flip
        window = diff[-10:]
        crossed_up = any(window[i] <= 0 < window[i + 1] for i in range(len(window) - 1))
        crossed_dn = any(window[i] >= 0 > window[i + 1] for i in range(len(window) - 1))
        if crossed_up:
            cross, cross_score = "Golden Cross", 9.0
        elif crossed_dn:
            cross, cross_score = "Death Cross", 1.5
        elif diff[-1] > 0:
            cross, cross_score = "Above (post-golden)", 6.5
        else:
            cross, cross_score = "Below (post-death)", 3.5

    # ── 3. SLOPE (20%) ───────────────────────────────────────────────────────
    e50_vals = [p["value"] for p in (ema_map.get(50) or [])][-20:]
    slope = _linear_slope(e50_vals)
    # Normalize slope to % of EMA level per day
    pct_slope = (slope / ema50 * 100) if ema50 else 0.0
    if pct_slope >= 0.15:    slope_score = 8.5
    elif pct_slope >= 0.05:  slope_score = 7.0
    elif pct_slope >= 0.0:   slope_score = 5.5
    elif pct_slope >= -0.05: slope_score = 4.5
    elif pct_slope >= -0.15: slope_score = 3.0
    else:                    slope_score = 1.5

    # ── 4. EXTENSION FROM EMA20 (20%) ────────────────────────────────────────
    ext_pct = (current_price - ema20) / ema20 * 100 if ema20 else 0.0
    # Slightly above is healthy; far above is overextended; below is a discount
    if ext_pct >= 12:     ext_score = 3.0   # overextended, pullback risk
    elif ext_pct >= 5:    ext_score = 5.5
    elif ext_pct >= 0:    ext_score = 7.5   # riding above the mean
    elif ext_pct >= -5:   ext_score = 6.0   # mild discount
    elif ext_pct >= -12:  ext_score = 4.5
    else:                 ext_score = 3.0   # deep below mean

    # ── WEIGHTED COMPOSITE ───────────────────────────────────────────────────
    raw = (
        stack_score * 0.35 +
        cross_score * 0.25 +
        slope_score * 0.20 +
        ext_score   * 0.20
    )

    # Volume confidence: expanding volume validates the move; contracting fades it
    if volume_confidence == "High":
        raw += 0.5 if raw > 5 else -0.5
    elif volume_confidence == "Low":
        raw += 1.0 if raw < 5 else -1.0

    score = round(max(1.0, min(10.0, raw)), 2)

    return {
        "score":   score,
        "stack":   stack_label,
        "cross":   cross,
        "ema20":   round(ema20, 2),
        "ema50":   round(ema50, 2),
        "ema200":  round(ema200, 2) if ema200 is not None else None,
        "slope_pct": round(pct_slope, 3),
        "ext_pct":   round(ext_pct, 2),
        "components": {
            "stack":     round(stack_score, 2),
            "cross":     round(cross_score, 2),
            "slope":     round(slope_score, 2),
            "extension": round(ext_score, 2),
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


def analyze(symbol, all_prices, all_volumes=None, ticker_details=None, ema_map=None):
    current_price, ma200, week52_high = compute_context(all_prices)
    pct_from_high = (week52_high - current_price) / week52_high * 100

    vol_profile = compute_volume_profile(all_volumes) if all_volumes else {
        "avg_20d": None, "avg_90d": None, "ratio": None, "confidence": "Normal"
    }

    ema_data = analyze_ema(ema_map, current_price, vol_profile["confidence"]) if ema_map else None
    td = ticker_details or {}

    composite_score = round(ema_data["score"], 1) if ema_data else None

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
        "ema": ema_data,
        "volume": vol_profile,
        "composite": {
            "score":          composite_score,
            "trend":          trend_label(round(composite_score)),
            "recommendation": buy_label(round(composite_score)),
        } if composite_score is not None else None,
    }


def print_result(result):
    ctx = result["context"]
    cap = result.get("cap", {})
    vol = result.get("volume", {})
    ema = result.get("ema") or {}
    above = "ABOVE ✓" if ctx["above_ma200"] else "BELOW ✗"

    mcap = cap.get("market_cap")
    mcap_str = f"${mcap/1e9:.1f}B" if mcap and mcap >= 1e9 else (f"${mcap/1e6:.0f}M" if mcap else "N/A")

    ratio = vol.get("ratio")
    vol_str = f"{vol.get('confidence', 'N/A')}  (20d/90d ratio: {ratio:.2f})" if ratio else vol.get("confidence", "N/A")

    print(f"\n{'='*62}")
    print(f"  EMA Analysis — {result['symbol']}  ({result.get('company', '')})")
    print(f"{'='*62}")
    print(f"  Cap Tier     : {cap.get('tier', 'Unknown')}  ({cap.get('description', '')})")
    print(f"  Market Cap   : {mcap_str}")
    print(f"  Price        : ${ctx['price']:.2f}")
    print(f"  200-day MA   : ${ctx['ma200']:.2f}  ({above})")
    print(f"  52-Week High : ${ctx['week52_high']:.2f}  ({ctx['pct_from_high']:.1f}% below high)")
    print(f"  Volume       : {vol_str}")
    print(f"")
    if ema.get("score") is not None:
        c = ema["components"]
        ema200_str = f"${ema['ema200']:.2f}" if ema["ema200"] is not None else "N/A"
        print(f"  EMA 20/50/200: ${ema['ema20']:.2f}  /  ${ema['ema50']:.2f}  /  {ema200_str}")
        print(f"  Stack        : {ema['stack']}")
        print(f"  Crossover    : {ema['cross']}")
        print(f"  EMA50 Slope  : {ema['slope_pct']:+.3f}% / day")
        print(f"  Extension    : {ema['ext_pct']:+.2f}% from EMA20")
        print(f"  Score        : {ema['score']}/10  "
              f"[stack:{c['stack']}×35%  cross:{c['cross']}×25%  "
              f"slope:{c['slope']}×20%  ext:{c['extension']}×20%]")
    else:
        print(f"  EMA          : N/A")

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

        ema_map = {w: fetch_ema_series(symbol, w) for w in EMA_WINDOWS}
        readings = ", ".join(f"{w}:{len(ema_map[w])}" for w in EMA_WINDOWS)
        print(f"  EMA readings : {readings}")
        print(f"  Price history: {len(prices)} trading days")

        result = analyze(symbol, prices, volumes, details, ema_map=ema_map)
        results.append(result)

    for result in results:
        print_result(result)

    if len(results) > 1:
        print_comparison(results)


def print_comparison(results):
    print(f"\n{'='*62}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*62}")
    print(f"  {'Symbol':<8} {'Cap Tier':<12} {'Cross':>14}  {'Score':>5}  {'Recommendation':<22} {'Vol'}")
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
            cross = r["ema"].get("cross", "N/A") if r.get("ema") else "N/A"
            vol_c = r["volume"].get("confidence", "N/A")
            rank  = sorted(tier_scores, reverse=True).index(score) + 1 if tier_scores else "-"
            rel   = f"#{rank}/{len(tier_scores)} in {tier}"
            print(f"  {r['symbol']:<8} {tier:<12} {cross:>14}  {score:>5.1f}  {rec:<22} {vol_c}  ({rel})")

    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
