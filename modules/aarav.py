import os
import requests
from datetime import datetime, timedelta

API_KEY = os.environ.get("POLYGON_API_KEY", "UPTtLEsTavIccF5ESguZSdtWW3zX93WW")
BASE_URL = "https://api.polygon.io"


def fetch_daily_prices(symbol, start_date, end_date):
    url = f"{BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        raise ValueError(f"Could not fetch data for {symbol}: {data.get('message') or data.get('status')}")
    return {
        datetime.utcfromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"): bar["c"]
        for bar in data["results"]
    }


def ema(values, period):
    k = 2 / (period + 1)
    result = []
    for i, v in enumerate(values):
        result.append(v if i == 0 else v * k + result[-1] * (1 - k))
    return result


def compute_macd(prices_dict, start_date, end_date):
    filtered = {d: p for d, p in prices_dict.items() if start_date <= d <= end_date}
    if len(filtered) < 35:
        return None
    dates = sorted(filtered.keys())
    closes = [filtered[d] for d in dates]

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    histogram = [m - s for m, s in zip(macd_line, signal_line)]

    return macd_line[-1], signal_line[-1], histogram[-1], macd_line, signal_line, histogram


def score_macd(macd, signal, hist, macd_line, signal_line, histogram_series):
    score = 5.0

    # Crossover: MACD above signal
    score += 1.5 if macd > signal else -1.5

    # Histogram acceleration (last 3 bars)
    if len(histogram_series) >= 3:
        r = histogram_series[-3:]
        if r[-1] > r[-2] > r[-3]:
            score += 1.5
        elif r[-1] < r[-2] < r[-3]:
            score -= 1.5

    # Zero-line position
    score += 0.5 if macd > 0 else -0.5

    # Histogram sign
    score += 0.5 if hist > 0 else -0.5

    # MACD momentum over last 5 bars
    if len(macd_line) >= 5:
        score += 0.5 if macd_line[-1] > macd_line[-5] else -0.5

    return max(1, min(10, round(score)))


def trend_label(score):
    if score >= 8:   return "Strong Uptrend  (Bullish)"
    if score >= 6:   return "Moderate Uptrend"
    if score >= 5:   return "Sideways / Neutral"
    if score >= 3:   return "Moderate Downtrend"
    return               "Strong Downtrend (Bearish)"


def buy_label(score):
    if score >= 8:   return "Strong Buy"
    if score >= 6:   return "Buy"
    if score >= 5:   return "Neutral / Hold"
    if score >= 3:   return "Sell / Avoid"
    return               "Strong Sell"


def analyze(symbol, all_prices):
    today = datetime.today().strftime("%Y-%m-%d")
    two_years_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    timeframes = [
        ("1 Month",  30),
        ("3 Months", 90),
        ("6 Months", 180),
        ("1 Year",   365),
        ("2 Years",  730),
    ]

    print(f"\n{'='*62}")
    print(f"  MACD Analysis — {symbol.upper()}")
    print(f"{'='*62}")

    scores = []
    for label, days in timeframes:
        start = max(
            (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d"),
            two_years_ago,
        )
        result = compute_macd(all_prices, start, today)
        if result is None:
            print(f"\n  [{label}]  Not enough data")
            continue

        macd, signal, hist, macd_line, signal_line, histogram_series = result
        score = score_macd(macd, signal, hist, macd_line, signal_line, histogram_series)
        scores.append(score)

        print(f"\n  Timeframe  : {label}")
        print(f"  MACD       : {macd:+.4f}")
        print(f"  Signal     : {signal:+.4f}")
        print(f"  Histogram  : {hist:+.4f}")
        print(f"  Score      : {score}/10")
        print(f"  Trend      : {trend_label(score)}")
        print(f"  Signal     : {buy_label(score)}")

    if scores:
        avg = sum(scores) / len(scores)
        avg_score = round(avg)
        print(f"\n{'─'*62}")
        print(f"  COMPOSITE SCORE  : {avg:.1f} / 10")
        print(f"  RECOMMENDATION   : {buy_label(avg_score)}")
        print(f"  OVERALL TREND    : {trend_label(avg_score)}")
        print(f"{'='*62}\n")


def main():
    symbol = input("Enter stock symbol (e.g. AAPL, TSLA): ").strip().upper()

    today = datetime.today().strftime("%Y-%m-%d")
    two_years_ago = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    print(f"\nFetching 2 years of data for {symbol}...")
    try:
        prices = fetch_daily_prices(symbol, two_years_ago, today)
    except ValueError as e:
        print(f"ERROR: {e}")
        return

    print(f"Retrieved {len(prices)} trading days.")
    analyze(symbol, prices)


if __name__ == "__main__":
    main()
