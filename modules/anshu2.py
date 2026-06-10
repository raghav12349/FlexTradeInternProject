import requests
from datetime import datetime

API_KEY = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"

EARLIEST_DATE  = "2015-01-01"
MAX_RANGE_DAYS = 730


def validate_dates(start_date, end_date):
    today    = datetime.today().date()
    earliest = datetime.strptime(EARLIEST_DATE, "%Y-%m-%d").date()
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end   = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return False, "Invalid date format. Please use YYYY-MM-DD."
    if start < earliest:
        return False, f"Start date is before {EARLIEST_DATE} — data not available before this date."
    if end > today:
        return False, f"End date {end_date} is in the future — please use today's date or earlier."
    if start >= end:
        return False, "Start date must be before end date."
    if (end - start).days < 90:
        return False, "Date range must be at least 3 months for a meaningful short interest analysis."
    if (end - start).days > MAX_RANGE_DAYS:
        return False, f"Date range is too long — maximum is 2 years (730 days). Short interest is a short-term sentiment indicator and loses accuracy over longer periods. Please shorten your date range."
    return True, None


def get_short_interest(ticker, start_date, end_date):
    url = (
        f"https://api.massive.com/stocks/v1/short-interest"
        f"?ticker={ticker}"
        f"&settlement_date.gte={start_date}"
        f"&settlement_date.lte={end_date}"
        f"&order=asc&limit=200"
    )
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r    = requests.get(url, headers=headers)
        data = r.json()
        return data.get("results", [])
    except Exception:
        return []


def get_free_float(ticker):
    url     = f"https://api.massive.com/stocks/vX/float?ticker={ticker}&limit=1"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r    = requests.get(url, headers=headers)
        data = r.json()
        if data.get("results"):
            return data["results"][0].get("free_float")
    except Exception:
        pass
    return None


def get_market_cap(ticker):
    url     = f"https://api.massive.com/stocks/financials/v1/ratios?ticker={ticker}&limit=1"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r    = requests.get(url, headers=headers)
        data = r.json()
        if data.get("results"):
            return data["results"][0].get("market_cap")
    except Exception:
        pass
    return None


def get_price_trend(ticker, start_date, end_date):
    url = (
        f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/month"
        f"/{start_date}/{end_date}?adjusted=true&order=asc&limit=50"
    )
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r       = requests.get(url, headers=headers)
        data    = r.json()
        results = data.get("results", [])
        if len(results) >= 2:
            first_close = results[0]["c"]
            last_close  = results[-1]["c"]
            pct_change  = ((last_close - first_close) / first_close) * 100
            return pct_change, first_close, last_close
    except Exception:
        pass
    return None, None, None


def rate_ticker(ticker: str, start_date: str, end_date: str) -> dict:
    result = {
        "ticker":           ticker,
        "score":            0,
        "signal":           "NEUTRAL",
        "days_to_cover":    None,
        "short_interest":   None,
        "short_pct_float":  None,
        "market_cap":       None,
        "cap_label":        None,
        "squeeze_alert":    False,
        "reasoning":        []
    }

    valid, error = validate_dates(start_date, end_date)
    if not valid:
        result["reasoning"].append(f"N/A — {error}")
        result["signal"] = "N/A"
        return result

    result["reasoning"].append(
        "Note: Short interest updates every 2 weeks — signal may lag real market conditions by up to 14 days."
    )

    records = get_short_interest(ticker, start_date, end_date)
    if not records:
        result["reasoning"].append("No short interest data found for this ticker in the given date range.")
        result["signal"] = "NO DATA"
        return result

    score       = 0
    latest      = records[-1]
    current_si  = latest.get("short_interest", 0)
    current_dtc = latest.get("days_to_cover", 0)
    current_vol = latest.get("avg_daily_volume", 0)

    result["days_to_cover"]  = round(current_dtc, 2) if current_dtc else None
    result["short_interest"] = current_si

    # Get market cap to adjust thresholds
    # Large caps naturally have higher days to cover due to massive volume
    # so we apply looser thresholds for them
    market_cap = get_market_cap(ticker)
    if market_cap:
        result["market_cap"] = market_cap
        if market_cap >= 200_000_000_000:
            cap_label  = "Mega Cap"
            dtc_low    = 2
            dtc_mod    = 6
            dtc_high   = 12
        elif market_cap >= 10_000_000_000:
            cap_label  = "Large Cap"
            dtc_low    = 2
            dtc_mod    = 5
            dtc_high   = 10
        elif market_cap >= 2_000_000_000:
            cap_label  = "Mid Cap"
            dtc_low    = 1.5
            dtc_mod    = 4
            dtc_high   = 8
        else:
            cap_label  = "Small Cap"
            dtc_low    = 1
            dtc_mod    = 3
            dtc_high   = 6
        result["cap_label"] = cap_label
        result["reasoning"].append(f"{cap_label} (${market_cap/1e9:.0f}B) — days to cover thresholds adjusted accordingly")
    else:
        cap_label = "Unknown"
        dtc_low   = 2
        dtc_mod   = 5
        dtc_high  = 10

    free_float = get_free_float(ticker)
    if free_float and free_float > 0 and current_si:
        pct_float             = (current_si / free_float) * 100
        result["short_pct_float"] = round(pct_float, 2)

        if pct_float <= 2:
            score += 3
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of free float — very lightly shorted, bullish (+3)")
        elif pct_float <= 5:
            score += 2
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of free float — low, mildly bullish (+2)")
        elif pct_float <= 10:
            score += 0
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of free float — normal range, neutral (0)")
        elif pct_float <= 20:
            score -= 2
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of free float — elevated, bearish (-2)")
        else:
            score -= 4
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of free float — very heavily shorted, strongly bearish (-4)")

    if current_dtc is not None:
        if current_dtc <= dtc_low:
            score += 2
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — low for a {cap_label} (+2)")
        elif current_dtc <= dtc_mod:
            score += 0
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — normal for a {cap_label} (0)")
        elif current_dtc <= dtc_high:
            score -= 2
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — elevated for a {cap_label} (-2)")
        else:
            score -= 4
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — extreme for a {cap_label} (-4)")

    if len(records) >= 4:
        mid       = len(records) // 2
        early_si  = sum(r.get("short_interest", 0) for r in records[:mid]) / mid
        recent_si = sum(r.get("short_interest", 0) for r in records[mid:]) / (len(records) - mid)

        if early_si > 0:
            si_trend_pct = ((recent_si - early_si) / early_si) * 100
            if si_trend_pct <= -20:
                score += 3
                result["reasoning"].append(f"Short interest declining {abs(si_trend_pct):.1f}% over period — shorts covering, bullish (+3)")
            elif si_trend_pct <= -5:
                score += 1
                result["reasoning"].append(f"Short interest slightly declining {abs(si_trend_pct):.1f}% — mild bullish signal (+1)")
            elif si_trend_pct <= 10:
                result["reasoning"].append(f"Short interest roughly flat ({si_trend_pct:+.1f}%) — no directional signal (0)")
            elif si_trend_pct <= 30:
                score -= 2
                result["reasoning"].append(f"Short interest rising {si_trend_pct:.1f}% — bears increasing positions (-2)")
            else:
                score -= 4
                result["reasoning"].append(f"Short interest surging {si_trend_pct:.1f}% — strong bearish accumulation (-4)")

    if len(records) >= 3:
        prev_si   = records[-2].get("short_interest", 0)
        spike_pct = ((current_si - prev_si) / prev_si * 100) if prev_si else 0
        if spike_pct >= 30:
            score -= 3
            result["reasoning"].append(f"Short interest spiked {spike_pct:.1f}% in last reporting period — sudden bearish signal (-3)")
        elif spike_pct >= 15:
            score -= 1
            result["reasoning"].append(f"Short interest jumped {spike_pct:.1f}% recently — watch closely (-1)")
        elif spike_pct <= -25:
            score += 2
            result["reasoning"].append(f"Short interest dropped {abs(spike_pct):.1f}% in last reporting period — shorts exiting fast (+2)")

    price_change, first_price, last_price = get_price_trend(ticker, start_date, end_date)

    if price_change is not None and current_dtc is not None:
        if current_dtc >= dtc_high and price_change > 5:
            score += 4
            result["squeeze_alert"] = True
            result["reasoning"].append(
                f"SHORT SQUEEZE SETUP — days to cover {current_dtc:.1f} AND price up {price_change:.1f}% — shorts are trapped (+4)"
            )
        elif current_dtc >= dtc_mod and price_change > 15:
            score += 3
            result["squeeze_alert"] = True
            result["reasoning"].append(
                f"POTENTIAL SQUEEZE — days to cover {current_dtc:.1f} AND price surging {price_change:.1f}% (+3)"
            )
        elif current_dtc >= dtc_high and price_change < -10:
            score -= 2
            result["reasoning"].append(
                f"High short interest + price falling {price_change:.1f}% — market confirming bearish view (-2)"
            )
        elif current_dtc <= dtc_low and price_change > 10:
            score += 2
            result["reasoning"].append(
                f"Low short interest + price rising {price_change:.1f}% — clean bullish setup (+2)"
            )

    if len(records) >= 4:
        early_dtc  = sum(r.get("days_to_cover", 0) for r in records[:len(records)//2]) / (len(records)//2)
        recent_dtc = sum(r.get("days_to_cover", 0) for r in records[len(records)//2:]) / (len(records) - len(records)//2)
        if early_dtc > 0:
            dtc_change = ((recent_dtc - early_dtc) / early_dtc) * 100
            if dtc_change <= -20:
                score += 2
                result["reasoning"].append(f"Days to cover improving over time — short pressure structurally easing (+2)")
            elif dtc_change >= 30:
                score -= 2
                result["reasoning"].append(f"Days to cover worsening over time — short pressure structurally building (-2)")

    if len(records) >= 4 and current_vol:
        early_vol  = sum(r.get("avg_daily_volume", 0) for r in records[:len(records)//2]) / (len(records)//2)
        if early_vol > 0:
            vol_change = ((current_vol - early_vol) / early_vol) * 100
            if vol_change >= 30 and score < 0:
                score -= 1
                result["reasoning"].append(f"Rising volume confirms short conviction — active bearish pressure (-1)")
            elif vol_change >= 30 and score > 0:
                score += 1
                result["reasoning"].append(f"Rising volume confirms buying conviction — active bullish pressure (+1)")

    result["score"] = score

    if result["squeeze_alert"]:
        result["signal"] = "SQUEEZE — STRONG BUY" if score >= 5 else "SQUEEZE — BUY"
    elif score >= 8:
        result["signal"] = "STRONG BUY"
    elif score >= 4:
        result["signal"] = "BUY"
    elif score >= 1:
        result["signal"] = "MILD BUY"
    elif score >= -2:
        result["signal"] = "NEUTRAL"
    elif score >= -5:
        result["signal"] = "CAUTION"
    else:
        result["signal"] = "AVOID"

    return result


def print_result(result: dict):
    print(f"\nSHORT INTEREST FACTOR ANALYSIS — {result['ticker']}")
    print(f"Signal:           {result['signal']}")
    print(f"Score:            {result['score']}")
    if result["cap_label"]:
        print(f"Company Size:     {result['cap_label']}")
    if result["days_to_cover"] is not None:
        print(f"Days to Cover:    {result['days_to_cover']}")
    if result["short_interest"] is not None:
        print(f"Short Interest:   {result['short_interest']:,} shares")
    if result["short_pct_float"] is not None:
        print(f"% of Free Float:  {result['short_pct_float']}%")
    if result["squeeze_alert"]:
        print(f"*** SHORT SQUEEZE ALERT ***")
    print(f"\nReasoning:")
    for r in result["reasoning"]:
        print(f"  • {r}")
    print()


if __name__ == "__main__":
    print("SHORT INTEREST FACTOR")

    print("Keep in mind:")
    print("  - Earliest start date: 2015-01-01")
    print("  - End date: today or earlier")
    print("  - Maximum date range: 2 years (e.g. 2024-06-10 to 2026-06-10)")
    print("  - Minimum date range: 3 months")
    print("  - Recommended range: 1-2 years for best accuracy")
    print()
    ticker     = input("Enter ticker (e.g. AAPL): ").strip().upper()
    start_date = input("Enter start date (YYYY-MM-DD): ").strip()
    end_date   = input("Enter end date   (YYYY-MM-DD): ").strip()

    print(f"\nAnalysing {ticker} from {start_date} to {end_date}...")
    result = rate_ticker(ticker, start_date, end_date)
    print_result(result)
