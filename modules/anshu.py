import requests
from polygon import RESTClient
from datetime import datetime

API_KEY       = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"
EARLIEST_DATE = "2015-01-01"


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
    if (end - start).days < 365:
        return False, "Date range must be at least 1 year."
    return True, None


def get_ratios(ticker):
    url     = f"https://api.massive.com/stocks/financials/v1/ratios?ticker={ticker}&limit=1"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r    = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data.get("results"):
            return data["results"][0]
    except Exception:
        pass
    return None


def rate_ticker(ticker: str, start_date: str, end_date: str) -> dict:
    client = RESTClient(API_KEY)
    result = {
        "ticker":       ticker,
        "score":        5,
        "signal":       "NEUTRAL",
        "payout_ratio": None,
        "market_cap":   None,
        "reasoning":    []
    }

    valid, error = validate_dates(start_date, end_date)
    if not valid:
        result["reasoning"].append(f"N/A — {error}")
        result["signal"] = "N/A"
        return result

    try:
        divs = list(client.list_dividends(
            ticker,
            ex_dividend_date_gte=start_date,
            ex_dividend_date_lte=end_date,
            limit=100,
            order="asc"
        ))
    except Exception as e:
        result["reasoning"].append(f"Failed to fetch dividends: {e}")
        result["signal"] = "ERROR"
        return result

    if not divs:
        result["reasoning"].append("No dividend history — growth stock, no income signal")
        result["signal"] = "NO DIVIDEND"
        result["score"]  = None
        return result

    yearly = {}
    for d in divs:
        year = str(d.ex_dividend_date)[:4]
        yearly[year] = yearly.get(year, 0) + d.cash_amount

    end_year  = end_date[:4]
    prev_year = str(int(end_year) - 1)

    full_years       = {y: v for y, v in yearly.items() if y != end_year}
    normal_frequency = 4
    if full_years:
        normal_frequency = max(
            sum(1 for d in divs if str(d.ex_dividend_date)[:4] == y)
            for y in full_years
        )

    current_count    = sum(1 for d in divs if str(d.ex_dividend_date)[:4] == end_year)
    yearly_projected = dict(yearly)
    if end_year in yearly and current_count > 0 and current_count < normal_frequency:
        yearly_projected[end_year] = (yearly[end_year] / current_count) * normal_frequency

    complete_years   = sorted(y for y in yearly if y != end_year)
    complete_amounts = [yearly[y] for y in complete_years]

    score = 5.0

    ratios     = get_ratios(ticker)
    payout_adj = 0
    if ratios:
        eps            = ratios.get("earnings_per_share")
        price          = ratios.get("price")
        dividend_yield = ratios.get("dividend_yield")
        market_cap     = ratios.get("market_cap")

        if eps and eps > 0 and price and dividend_yield:
            annual_div   = price * dividend_yield
            payout_ratio = (annual_div / eps) * 100
            result["payout_ratio"] = round(payout_ratio, 1)

            if payout_ratio < 25:
                payout_adj = +2.0
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — exceptionally low, very sustainable (+2.0)")
            elif payout_ratio < 40:
                payout_adj = +1.5
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — very sustainable (+1.5)")
            elif payout_ratio < 55:
                payout_adj = +0.5
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — healthy (+0.5)")
            elif payout_ratio < 70:
                payout_adj = -0.5
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — elevated (-0.5)")
            elif payout_ratio < 85:
                payout_adj = -1.5
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — high, dividend at risk (-1.5)")
            else:
                payout_adj = -2.5
                result["reasoning"].append(f"Payout ratio {payout_ratio:.1f}% — dangerously high (-2.5)")
        else:
            result["reasoning"].append("Payout ratio not available")

        # Market cap context
        if market_cap:
            result["market_cap"] = market_cap
            if market_cap >= 200_000_000_000:
                score += 0.3
                result["reasoning"].append(f"Mega cap (${market_cap/1e9:.0f}B) — dividend commitment very credible (+0.3)")
            elif market_cap >= 10_000_000_000:
                score += 0.0
                result["reasoning"].append(f"Large cap (${market_cap/1e9:.0f}B) — dividend credible (0)")
            elif market_cap >= 2_000_000_000:
                score += 0.2
                result["reasoning"].append(f"Mid cap (${market_cap/1e9:.1f}B) — paying dividends at this size is positive (+0.2)")
            else:
                score -= 0.5
                result["reasoning"].append(f"Small cap (${market_cap/1e9:.1f}B) — higher dividend cut risk (-0.5)")

    score += payout_adj

  
    consecutive = 0
    best_streak = 0
    for i in range(1, len(complete_amounts)):
        if complete_amounts[i] > complete_amounts[i - 1]:
            consecutive += 1
            best_streak = max(best_streak, consecutive)
        else:
            consecutive = 0

    streak_adj = 0
    if best_streak >= 8:
        streak_adj = +1.5
        result["reasoning"].append(f"{best_streak} consecutive years of growth (+1.5)")
    elif best_streak >= 5:
        streak_adj = +1.0
        result["reasoning"].append(f"{best_streak} consecutive years of growth (+1.0)")
    elif best_streak >= 3:
        streak_adj = +0.5
        result["reasoning"].append(f"{best_streak} consecutive years of growth (+0.5)")
    elif best_streak >= 1:
        streak_adj = +0.2
        result["reasoning"].append(f"{best_streak} year(s) of growth (+0.2)")
    else:
        streak_adj = -0.5
        result["reasoning"].append("No consecutive growth streak (-0.5)")

    if best_streak >= 3 and result["payout_ratio"] and result["payout_ratio"] > 70:
        streak_adj -= 0.8
        result["reasoning"].append(f"Long streak undermined by high payout ratio (-0.8)")

    score += streak_adj

   
    if end_year in yearly_projected and prev_year in yearly:
        change = yearly_projected[end_year] - yearly[prev_year]
        pct    = (change / yearly[prev_year]) * 100 if yearly[prev_year] else 0

        if pct >= 10:
            score += 0.8
            result["reasoning"].append(f"Dividend grew {pct:.1f}% vs last year — strong (+0.8)")
        elif pct >= 3:
            score += 0.4
            result["reasoning"].append(f"Dividend grew {pct:.1f}% vs last year (+0.4)")
        elif pct > 0:
            score += 0.1
            result["reasoning"].append(f"Dividend grew {pct:.1f}% vs last year — minimal (+0.1)")
        elif pct < -10:
            score -= 1.5
            result["reasoning"].append(f"Dividend cut {abs(pct):.1f}% vs last year (-1.5)")
        elif pct < 0:
            score -= 0.8
            result["reasoning"].append(f"Dividend reduced {abs(pct):.1f}% vs last year (-0.8)")
        else:
            result["reasoning"].append("Dividend flat vs last year (0)")

    if len(complete_years) >= 2:
        expected = set(str(y) for y in range(int(complete_years[0]), int(complete_years[-1]) + 1))
        missing  = expected - set(complete_years)
        if missing:
            score -= 1.0
            result["reasoning"].append(f"Dividend suspended in {', '.join(sorted(missing))} (-1.0)")

   
    if len(complete_amounts) >= 4:
        mid           = len(complete_amounts) // 2
        early_growth  = (complete_amounts[mid] - complete_amounts[0]) / complete_amounts[0] * 100 if complete_amounts[0] else 0
        recent_growth = (complete_amounts[-1] - complete_amounts[mid]) / complete_amounts[mid] * 100 if complete_amounts[mid] else 0
        if recent_growth > 0 and early_growth > 0:
            if recent_growth > early_growth * 1.2:
                score += 0.3
                result["reasoning"].append(f"Growth accelerating ({recent_growth:.1f}% recent vs {early_growth:.1f}% earlier) (+0.3)")
            elif recent_growth < early_growth * 0.4:
                score -= 0.3
                result["reasoning"].append(f"Growth slowing ({recent_growth:.1f}% recent vs {early_growth:.1f}% earlier) (-0.3)")
            else:
                result["reasoning"].append(f"Growth steady ({recent_growth:.1f}% recent vs {early_growth:.1f}% earlier) (0)")

    score = round(max(1.0, min(10.0, score)), 1)
    result["score"] = score

    if score >= 8.0:
        result["signal"] = "STRONG BUY"
    elif score >= 6.5:
        result["signal"] = "BUY"
    elif score >= 5.5:
        result["signal"] = "MILD BUY"
    elif score >= 4.5:
        result["signal"] = "NEUTRAL"
    elif score >= 3.0:
        result["signal"] = "CAUTION"
    else:
        result["signal"] = "AVOID"

    return result


def print_result(result: dict):
    print(f"\nDIVIDEND FACTOR ANALYSIS — {result['ticker']}")
    print(f"Signal:       {result['signal']}")
    print(f"Score:        {result['score']}/10")
    print(f"Payout Ratio: {str(result['payout_ratio']) + '%' if result['payout_ratio'] is not None else 'N/A'}")
    if result.get("market_cap"):
        print(f"Market Cap:   ${result['market_cap']/1e9:.1f}B")
    print(f"\nReasoning:")
    for r in result["reasoning"]:
        print(f"  • {r}")
    print()


if __name__ == "__main__":
    print("DIVIDEND GROWTH + PAYOUT RATIO FACTOR")
    ticker     = input("Enter ticker (e.g. AAPL): ").strip().upper()
    start_date = input("Enter start date (YYYY-MM-DD, earliest 2015-01-01): ").strip()
    end_date   = input("Enter end date   (YYYY-MM-DD, latest today): ").strip()
    print(f"\nAnalysing {ticker} from {start_date} to {end_date}...")
    result = rate_ticker(ticker, start_date, end_date)
    print_result(result)
