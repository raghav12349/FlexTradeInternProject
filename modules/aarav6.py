import os
import sys
from datetime import datetime, timedelta

try:
    from massive import RESTClient
except ImportError:
    from polygon import RESTClient

SIGNAL_NAME     = "short_volume"
SIGNAL_OWNER    = "aarav"
SIGNAL_CATEGORY = "Technicals"

def _load_massive_key():
    k = os.environ.get("MASSIVE_API_KEY")
    if k:
        return k
    import pathlib
    env_file = pathlib.Path(__file__).parent.parent / ".keys.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("MASSIVE_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("MASSIVE_API_KEY not set — add it to .keys.env or export it")

client = RESTClient(_load_massive_key())

# Market cap thresholds (USD)
CAP_TIERS = [
    (200_000_000_000, "Mega Cap",  "Top-tier blue chips (>$200B)"),
    ( 10_000_000_000, "Large Cap", "$10B–$200B"),
    (  2_000_000_000, "Mid Cap",   "$2B–$10B"),
    (              0, "Small Cap", "<$2B"),
]

# Score → signal thresholds (same as aarav3/4)
# NOTE: HIGH short volume = bearish → LOW score. LOW short volume / covering → HIGH score.
def trend_label(score):
    if score >= 8: return "Heavy Covering (Bullish)"
    if score >= 6: return "Moderate Covering"
    if score >= 5: return "Neutral Short Activity"
    if score >= 3: return "Elevated Short Pressure"
    return "Extreme Short Pressure (Bearish)"

def buy_label(score):
    if score >= 8: return "Strong Buy"
    if score >= 6: return "Buy"
    if score >= 5: return "Neutral / Hold"
    if score >= 3: return "Sell / Avoid"
    return "Strong Sell"


def classify_cap(market_cap):
    if market_cap is None:
        return "Unknown", "Market cap unavailable"
    for threshold, label, desc in CAP_TIERS:
        if market_cap >= threshold:
            return label, desc
    return "Small Cap", "<$2B"


def fetch_ticker_details(symbol):
    try:
        from polygon import RESTClient as PolyClient
        poly_key = os.environ.get("POLYGON_API_KEY", "")
        if poly_key:
            pclient = PolyClient(poly_key)
            d = pclient.get_ticker_details(symbol)
            name = getattr(d, "name", symbol)
            market_cap = getattr(d, "market_cap", None)
        else:
            name, market_cap = symbol, None
    except Exception:
        name, market_cap = symbol, None
    cap_tier, cap_desc = classify_cap(market_cap)
    return {"name": name, "market_cap": market_cap, "cap_tier": cap_tier, "cap_desc": cap_desc}


def fetch_short_volume(symbol, days=30):
    """
    Fetch recent short volume records for a symbol via list_short_volume().
    Returns list of dicts with keys: date, short_volume, short_exempt_volume, total_volume.
    Sorted oldest → newest.
    """
    today = datetime.today()
    start = (today - timedelta(days=days + 14)).strftime("%Y-%m-%d")  # buffer for weekends/holidays
    end   = today.strftime("%Y-%m-%d")

    records = []
    try:
        for item in client.list_short_volume(
            ticker=symbol,
            date_gte=start,
            date_lte=end,
            limit=50,
            sort="date.asc",
        ):
            date    = getattr(item, "date", None) or getattr(item, "timestamp", None)
            sv      = getattr(item, "short_volume", 0) or 0
            exempt  = getattr(item, "short_exempt_volume", 0) or 0
            total   = getattr(item, "total_volume", 0) or 0
            if date and total > 0:
                records.append({
                    "date":          str(date)[:10],
                    "short_volume":  sv,
                    "short_exempt":  exempt,
                    "total_volume":  total,
                })
    except Exception as e:
        raise ValueError(f"Could not fetch short volume data for {symbol}: {e}")

    if not records:
        raise ValueError(f"No short volume data returned for {symbol}")

    records.sort(key=lambda r: r["date"])
    return records[-days:]  # trim to requested window


def analyze_short_volume(records):
    """
    Trader-grade short volume analysis using 4 weighted components.

    Components and weights:
      40%  Short Volume Ratio (SVR)   — short_vol / total_vol (higher = more bearish)
      30%  SVR Trend                  — is ratio rising or falling over ~10 days
      15%  Exempt Ratio               — exempt / short_vol (high exempt = MM hedging, less bearish)
      15%  Volume Surge               — recent avg short vol vs baseline (surge = bearish)

    Score inverting: HIGH short pressure → LOW score (bearish / sell signal).
    """
    if len(records) < 5:
        return None

    ratios  = [r["short_volume"] / r["total_volume"] for r in records]
    exempts = [r["short_exempt"] / r["short_volume"] if r["short_volume"] else 0 for r in records]
    svols   = [r["short_volume"] for r in records]

    latest_ratio   = ratios[-1]
    avg_ratio      = sum(ratios) / len(ratios)
    latest_exempt  = exempts[-1]

    # ── 1. SHORT VOLUME RATIO (40%) ──────────────────────────────────────────
    # Typical SVR range: 0.30–0.65. Above 0.55 = heavy shorting.
    if latest_ratio >= 0.65:   svr_score = 1.5   # extreme short pressure
    elif latest_ratio >= 0.55: svr_score = 3.0   # heavy
    elif latest_ratio >= 0.48: svr_score = 4.5   # elevated
    elif latest_ratio >= 0.42: svr_score = 5.5   # neutral
    elif latest_ratio >= 0.35: svr_score = 7.0   # light — bulls in control
    else:                      svr_score = 9.0   # very low shorting / covering

    # ── 2. SVR TREND (30%) ───────────────────────────────────────────────────
    # Compare last 5-day avg vs prior 5-day avg
    if len(ratios) >= 10:
        recent   = sum(ratios[-5:]) / 5
        prior    = sum(ratios[-10:-5]) / 5
        delta    = recent - prior
    else:
        recent = ratios[-1]
        prior  = ratios[0]
        delta  = recent - prior

    if delta >= 0.10:    trend_score = 1.5   # sharply rising shorts = bearish
    elif delta >= 0.05:  trend_score = 3.0
    elif delta >= 0.01:  trend_score = 4.5
    elif delta >= -0.01: trend_score = 5.5   # stable
    elif delta >= -0.05: trend_score = 7.0
    else:                trend_score = 9.0   # sharply falling shorts = covering = bullish

    # ── 3. EXEMPT RATIO (15%) ────────────────────────────────────────────────
    # High exempt = market-maker hedging, not directional short pressure → less bearish
    if latest_exempt >= 0.30:  exempt_score = 7.5   # mostly MM activity
    elif latest_exempt >= 0.15: exempt_score = 6.0
    elif latest_exempt >= 0.05: exempt_score = 5.0
    else:                       exempt_score = 3.5   # almost pure directional short

    # ── 4. VOLUME SURGE (15%) ────────────────────────────────────────────────
    # Recent 5-day avg short vol vs 20-day avg — surge amplifies bearish signal
    avg_20 = sum(svols[-20:]) / min(20, len(svols))
    avg_5  = sum(svols[-5:]) / 5
    surge  = avg_5 / avg_20 if avg_20 else 1.0

    if surge >= 2.0:    surge_score = 1.5   # volume surge into shorts = very bearish
    elif surge >= 1.5:  surge_score = 3.0
    elif surge >= 1.2:  surge_score = 4.5
    elif surge >= 0.8:  surge_score = 5.5   # normal range
    elif surge >= 0.5:  surge_score = 7.0
    else:               surge_score = 8.5   # short volume drying up = shorts covering

    # ── WEIGHTED COMPOSITE ───────────────────────────────────────────────────
    raw = (
        svr_score    * 0.40 +
        trend_score  * 0.30 +
        exempt_score * 0.15 +
        surge_score  * 0.15
    )
    score = round(max(1.0, min(10.0, raw)), 2)

    return {
        "score":          score,
        "latest_ratio":   round(latest_ratio, 4),
        "avg_ratio":      round(avg_ratio, 4),
        "ratio_delta":    round(delta, 4),
        "latest_exempt":  round(latest_exempt, 4),
        "surge":          round(surge, 2),
        "days":           len(records),
        "components": {
            "svr":    round(svr_score, 2),
            "trend":  round(trend_score, 2),
            "exempt": round(exempt_score, 2),
            "surge":  round(surge_score, 2),
        },
    }


def analyze(symbol, period="2y", **_):
    """Signal contract entry point — returns normalized score in [-1, 1]."""
    records = fetch_short_volume(symbol, days=30)
    sv_data = analyze_short_volume(records)
    if sv_data is None:
        return {"ticker": symbol.upper(), "signal": SIGNAL_NAME, "score": 0.0,
                "rating": "Neutral / Hold", "details": {}}

    score_1_10 = sv_data["score"]
    normalized = round((score_1_10 - 5.5) / 4.5, 4)  # maps [1,10] → [-1, +1]

    from core.rating import score_to_rating  # noqa: E402  (optional dep)
    try:
        rating = score_to_rating(normalized)
    except Exception:
        rating = buy_label(round(score_1_10))

    return {
        "ticker":  symbol.upper(),
        "signal":  SIGNAL_NAME,
        "score":   normalized,
        "rating":  rating,
        "details": sv_data,
    }


def _analyze_full(symbol):
    """Internal: full result dict used by print_result / print_comparison."""
    records = fetch_short_volume(symbol, days=30)
    sv_data = analyze_short_volume(records)

    td = fetch_ticker_details(symbol)

    composite_score = round(sv_data["score"], 1) if sv_data else None

    return {
        "symbol":  symbol.upper(),
        "company": td.get("name", symbol.upper()),
        "cap": {
            "tier":        td.get("cap_tier", "Unknown"),
            "description": td.get("cap_desc", ""),
            "market_cap":  td.get("market_cap"),
        },
        "short": sv_data,
        "composite": {
            "score":          composite_score,
            "trend":          trend_label(round(composite_score)),
            "recommendation": buy_label(round(composite_score)),
        } if composite_score is not None else None,
    }


def print_result(result):
    cap  = result.get("cap", {})
    sv   = result.get("short") or {}

    mcap = cap.get("market_cap")
    mcap_str = f"${mcap/1e9:.1f}B" if mcap and mcap >= 1e9 else (f"${mcap/1e6:.0f}M" if mcap else "N/A")

    print(f"\n{'='*62}")
    print(f"  Short Volume Analysis — {result['symbol']}  ({result.get('company', '')})")
    print(f"{'='*62}")
    print(f"  Cap Tier     : {cap.get('tier', 'Unknown')}  ({cap.get('description', '')})")
    print(f"  Market Cap   : {mcap_str}")
    print(f"")

    if sv.get("score") is not None:
        c = sv["components"]
        sign = "+" if sv["ratio_delta"] >= 0 else ""
        print(f"  Short/Total Ratio   : {sv['latest_ratio']:.2%}  (avg {sv['avg_ratio']:.2%})")
        print(f"  Ratio Trend (5d/5d) : {sign}{sv['ratio_delta']:.2%}")
        print(f"  Exempt Ratio        : {sv['latest_exempt']:.2%}  (MM/hedging share)")
        print(f"  Short Vol Surge     : {sv['surge']:.2f}×  (5d avg vs 20d avg)")
        print(f"  Days analyzed       : {sv['days']}")
        print(f"  Score               : {sv['score']}/10  "
              f"[svr:{c['svr']}×40%  trend:{c['trend']}×30%  "
              f"exempt:{c['exempt']}×15%  surge:{c['surge']}×15%]")
    else:
        print(f"  Short Volume : N/A")

    if result.get("composite"):
        c = result["composite"]
        print(f"\n{'─'*62}")
        print(f"  COMPOSITE SCORE  : {c['score']} / 10")
        print(f"  RECOMMENDATION   : {c['recommendation']}")
        print(f"  SHORT SIGNAL     : {c['trend']}")
    print(f"{'='*62}\n")


def print_comparison(results):
    print(f"\n{'='*62}")
    print(f"  SHORT VOLUME COMPARISON SUMMARY")
    print(f"{'='*62}")
    print(f"  {'Symbol':<8} {'Cap Tier':<12} {'SVR':>6}  {'Trend':>7}  {'Score':>5}  {'Recommendation':<22}")
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
            sv    = r.get("short") or {}
            score = comp["score"]
            rec   = comp["recommendation"]
            svr   = f"{sv.get('latest_ratio', 0):.1%}" if sv.get("latest_ratio") is not None else "N/A"
            delta = sv.get("ratio_delta", 0)
            sign  = "+" if delta >= 0 else ""
            trend_str = f"{sign}{delta:.2%}"
            rank  = sorted(tier_scores, reverse=True).index(score) + 1 if tier_scores else "-"
            rel   = f"#{rank}/{len(tier_scores)} in {tier}"
            print(f"  {r['symbol']:<8} {tier:<12} {svr:>6}  {trend_str:>7}  {score:>5.1f}  {rec:<22}  ({rel})")

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

    results = []
    for symbol in symbols:
        print(f"\nFetching short volume data for {symbol}...")
        try:
            result = _analyze_full(symbol)
            sv = result.get("short")
            if sv:
                print(f"  {result['company']}  |  {result['cap']['tier']}  |  {sv['days']} days of data")
            results.append(result)
        except ValueError as e:
            print(f"  ERROR: {e}")
            continue

    for result in results:
        print_result(result)

    if len(results) > 1:
        print_comparison(results)


if __name__ == "__main__":
    main()
