import requests
from datetime import datetime

API_KEY       = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"
EARLIEST_DATE = "2015-01-01"
MAX_RANGE_DAYS = 365


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
        return False, f"End date is in the future — please use today's date or earlier."
    if start >= end:
        return False, "Start date must be before end date."
    if (end - start).days < 60:
        return False, "Date range must be at least 2 months."
    if (end - start).days > MAX_RANGE_DAYS:
        return False, f"Maximum range is 1 year — short interest is a short-term indicator and loses accuracy beyond this."
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
        r    = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        return data.get("results", [])
    except Exception:
        return []


def get_free_float(ticker):
    url     = f"https://api.massive.com/stocks/vX/float?ticker={ticker}&limit=1"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r    = requests.get(url, headers=headers, timeout=15)
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
        r    = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data.get("results"):
            return data["results"][0].get("market_cap")
    except Exception:
        pass
    return None


def get_price_trend(ticker, start_date, end_date):
    url = (
        f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/month"
        f"/{start_date}/{end_date}?adjusted=true&order=asc&limit=15"
    )
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r       = requests.get(url, headers=headers, timeout=15)
        data    = r.json()
        results = data.get("results", [])
        if len(results) >= 2:
            first = results[0]["c"]
            last  = results[-1]["c"]
            return round(((last - first) / first) * 100, 2)
    except Exception:
        pass
    return None


def rate_ticker(ticker: str, start_date: str, end_date: str) -> dict:
    result = {
        "ticker":          ticker,
        "score":           5.0,
        "signal":          "NEUTRAL",
        "days_to_cover":   None,
        "short_interest":  None,
        "short_pct_float": None,
        "cap_label":       None,
        "squeeze_alert":   False,
        "reasoning":       []
    }

    valid, error = validate_dates(start_date, end_date)
    if not valid:
        result["reasoning"].append(f"N/A — {error}")
        result["signal"] = "N/A"
        return result

    result["reasoning"].append(
        "Note: Short interest updates every 2 weeks — signal may lag up to 14 days."
    )

    records = get_short_interest(ticker, start_date, end_date)
    if not records:
        result["reasoning"].append("No short interest data found.")
        result["signal"] = "NO DATA"
        return result

    score       = 5.0
    latest      = records[-1]
    current_si  = latest.get("short_interest", 0)
    current_dtc = latest.get("days_to_cover", 0)
    current_vol = latest.get("avg_daily_volume", 0)

    result["days_to_cover"]  = round(current_dtc, 2) if current_dtc else None
    result["short_interest"] = current_si

    market_cap = get_market_cap(ticker)
    if market_cap:
        if market_cap >= 200_000_000_000:
            cap_label = "Mega Cap"
            dtc_low, dtc_mod, dtc_high = 2, 6, 12
        elif market_cap >= 10_000_000_000:
            cap_label = "Large Cap"
            dtc_low, dtc_mod, dtc_high = 2, 5, 10
        elif market_cap >= 2_000_000_000:
            cap_label = "Mid Cap"
            dtc_low, dtc_mod, dtc_high = 1.5, 4, 8
        else:
            cap_label = "Small Cap"
            dtc_low, dtc_mod, dtc_high = 1, 3, 6
        result["cap_label"] = cap_label
        result["reasoning"].append(f"{cap_label} (${market_cap/1e9:.0f}B) — thresholds adjusted")
    else:
        cap_label = "Unknown"
        dtc_low, dtc_mod, dtc_high = 2, 5, 10


    free_float = get_free_float(ticker)
    if free_float and free_float > 0 and current_si:
        pct_float              = (current_si / free_float) * 100
        result["short_pct_float"] = round(pct_float, 2)

        if pct_float <= 1.5:
            score += 1.5
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — extremely lightly shorted (+1.5)")
        elif pct_float <= 3:
            score += 1.0
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — very lightly shorted (+1.0)")
        elif pct_float <= 6:
            score += 0.3
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — low, mildly bullish (+0.3)")
        elif pct_float <= 12:
            score -= 0.5
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — normal/moderate (-0.5)")
        elif pct_float <= 20:
            score -= 1.5
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — elevated, bearish (-1.5)")
        else:
            score -= 2.5
            result["reasoning"].append(f"Short interest {pct_float:.1f}% of float — extremely heavily shorted (-2.5)")
    else:
        result["reasoning"].append("Free float data not available — skipping float % signal")
        pct_float = None

 
    if len(records) >= 4:
        mid       = len(records) // 2
        early_si  = sum(r.get("short_interest", 0) for r in records[:mid]) / mid
        recent_si = sum(r.get("short_interest", 0) for r in records[mid:]) / (len(records) - mid)

        if early_si > 0:
            si_trend = ((recent_si - early_si) / early_si) * 100

            if si_trend <= -25:
                score += 1.5
                result["reasoning"].append(f"Short interest falling {abs(si_trend):.1f}% — shorts covering fast (+1.5)")
            elif si_trend <= -10:
                score += 0.8
                result["reasoning"].append(f"Short interest declining {abs(si_trend):.1f}% — bullish trend (+0.8)")
            elif si_trend <= 5:
                result["reasoning"].append(f"Short interest flat ({si_trend:+.1f}%) — no directional signal (0)")
            elif si_trend <= 20:
                score -= 0.8
                result["reasoning"].append(f"Short interest rising {si_trend:.1f}% — bears building positions (-0.8)")
            elif si_trend <= 40:
                score -= 1.5
                result["reasoning"].append(f"Short interest surging {si_trend:.1f}% — strong bearish accumulation (-1.5)")
            else:
                score -= 2.0
                result["reasoning"].append(f"Short interest exploding {si_trend:.1f}% — extreme bearish conviction (-2.0)")

   
    if current_dtc:
        if current_dtc <= dtc_low:
            score += 0.8
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — low for {cap_label} (+0.8)")
        elif current_dtc <= dtc_mod:
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — normal for {cap_label} (0)")
        elif current_dtc <= dtc_high:
            score -= 0.8
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — elevated for {cap_label} (-0.8)")
        else:
            score -= 1.5
            result["reasoning"].append(f"Days to cover {current_dtc:.1f} — extreme for {cap_label} (-1.5)")

   
    if len(records) >= 3:
        prev_si   = records[-2].get("short_interest", 0)
        spike_pct = ((current_si - prev_si) / prev_si * 100) if prev_si else 0

        if spike_pct >= 30:
            score -= 1.2
            result["reasoning"].append(f"Sudden spike: short interest jumped {spike_pct:.1f}% last period (-1.2)")
        elif spike_pct >= 15:
            score -= 0.5
            result["reasoning"].append(f"Short interest up {spike_pct:.1f}% last period — watch closely (-0.5)")
        elif spike_pct <= -25:
            score += 1.0
            result["reasoning"].append(f"Sudden drop: short interest fell {abs(spike_pct):.1f}% last period (+1.0)")
        elif spike_pct <= -10:
            score += 0.4
            result["reasoning"].append(f"Short interest down {abs(spike_pct):.1f}% last period (+0.4)")

  
    price_change = get_price_trend(ticker, start_date, end_date)

    if price_change is not None and current_dtc:
        if current_dtc >= dtc_high and price_change > 5:
            score += 2.0
            result["squeeze_alert"] = True
            result["reasoning"].append(
                f"SHORT SQUEEZE SETUP — DTC {current_dtc:.1f} + price up {price_change:.1f}% — shorts trapped (+2.0)"
            )
        elif current_dtc >= dtc_mod and price_change > 15:
            score += 1.5
            result["squeeze_alert"] = True
            result["reasoning"].append(
                f"POTENTIAL SQUEEZE — DTC {current_dtc:.1f} + price surging {price_change:.1f}% (+1.5)"
            )
        elif current_dtc >= dtc_high and price_change < -10:
            score -= 1.0
            result["reasoning"].append(
                f"High shorts + price falling {price_change:.1f}% — market confirming bearish view (-1.0)"
            )
        elif current_dtc <= dtc_low and price_change > 10:
            score += 0.8
            result["reasoning"].append(
                f"Low shorts + price rising {price_change:.1f}% — clean bullish setup (+0.8)"
            )

    score          = round(max(1.0, min(10.0, score)), 1)
    result["score"] = score

    if result["squeeze_alert"]:
        result["signal"] = "SQUEEZE — STRONG BUY" if score >= 7.5 else "SQUEEZE — BUY"
    elif score >= 7.5:
        result["signal"] = "STRONG BUY"
    elif score >= 6.2:
        result["signal"] = "BUY"
    elif score >= 5.2:
        result["signal"] = "MILD BUY"
    elif score >= 4.2:
        result["signal"] = "NEUTRAL"
    elif score >= 3.0:
        result["signal"] = "CAUTION"
    else:
        result["signal"] = "AVOID"

    return result


def print_result(result: dict):
    print(f"\nSHORT INTEREST FACTOR — {result['ticker']}")
    print(f"Signal:           {result['signal']}")
    print(f"Score:            {result['score']}/10")
    if result["cap_label"]:
        print(f"Company Size:     {result['cap_label']}")
    if result["days_to_cover"] is not None:
        print(f"Days to Cover:    {result['days_to_cover']}")
    if result["short_interest"] is not None:
        print(f"Short Interest:   {result['short_interest']:,} shares")
    if result["short_pct_float"] is not None:
        print(f"% of Free Float:  {result['short_pct_float']}%")
    if result["squeeze_alert"]:
        print("*** SHORT SQUEEZE ALERT ***")
    print("\nReasoning:")
    for r in result["reasoning"]:
        print(f"  • {r}")
    print()


if __name__ == "__main__":
    print("SHORT INTEREST FACTOR")
    print("---------------------")
    print("Keep in mind:")
    print("  - Earliest start date: 2015-01-01")
    print("  - Maximum range: 1 year")
    print("  - Minimum range: 2 months")
    print("  - Recommended: 6-12 months for best accuracy")
    print()
    ticker     = input("Enter ticker (e.g. AAPL): ").strip().upper()
    start_date = input("Enter start date (YYYY-MM-DD): ").strip()
    end_date   = input("Enter end date   (YYYY-MM-DD): ").strip()
    print(f"\nAnalysing {ticker} from {start_date} to {end_date}...")
    result = rate_ticker(ticker, start_date, end_date)
    print_result(result)
