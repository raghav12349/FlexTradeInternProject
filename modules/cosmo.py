#!/usr/bin/env python3
"""
Form 4 Insider Signal Tracker — Massive.com REST API
Outputs: signal (BULLISH / BEARISH / NEUTRAL) + reason.

Usage:
    python3 form4_insider_tracker.py --ticker AAPL
    python3 form4_insider_tracker.py --ticker TSLA --from-date 2025-01-01 --to-date 2025-06-10
    python3 form4_insider_tracker.py --ticker NVDA --days 180
"""

import argparse
import math
import sys
import warnings
from datetime import date, timedelta
from typing import Optional

# Suppress urllib3/LibreSSL version warning on macOS
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: run  pip3 install requests")

# ─── HARD-CODE YOUR API KEY HERE ─────────────────────────────────────────────
API_KEY = "UPTtLEsTavIccF5ESguZSdtWW3zX93WW"
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://api.massive.com"
ENDPOINT = "/stocks/filings/vX/form-4"

BULLISH_CODES = {"P"}   # open-market purchase
BEARISH_CODES = {"S"}   # open-market sale

ROLE_WEIGHTS = {
    "CEO": 1.5, "PRESIDENT": 1.4, "CHAIRMAN": 1.4,
    "CFO": 1.3, "COO": 1.2,
    "DIRECTOR": 1.0, "OFFICER": 1.0, "10%": 0.8,
}

# ─── SIGNAL THRESHOLDS ───────────────────────────────────────────────────────
#
# Sells are discounted — insiders sell for many reasons unrelated to outlook.
SELL_DISCOUNT = 0.25
#
# Thresholds for buy/sell conviction scores.
BULLISH_THRESHOLD =  0.25
BEARISH_THRESHOLD = -0.70
#
# BEARISH is only triggered if total sell value exceeds this fraction of the
# company's market cap. This prevents large-cap mega-sells from looking scary
# when they're actually a tiny fraction of the company's overall value.
# e.g. 0.0005 = 0.05% of market cap — even $20M at Tesla is well below this.
BEARISH_MIN_MCAP_FRACTION = 0.0005   # 0.05% of market cap
#
# BEARISH also requires at least this many distinct sellers, so that a single
# insider's routine plan-based selling cannot trigger the label.
BEARISH_MIN_SELLERS = 3


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def role_weight(title: str) -> float:
    up = (title or "").upper()
    for keyword, weight in ROLE_WEIGHTS.items():
        if keyword in up:
            return weight
    return 1.0


def fmt_usd(n) -> str:
    if not n:
        return "$0"
    n = float(n)
    if abs(n) >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.1f}K"
    return f"${n:.2f}"


def fetch_market_cap(ticker: str) -> Optional[float]:
    """
    Fetches market cap from the Massive Ticker Overview endpoint:
      GET /v3/reference/tickers/{ticker}
    Response: { results: { market_cap: float, ... } }
    Returns None if unavailable — the BEARISH market-cap gate is then skipped.
    """
    url = f"{BASE_URL}/v3/reference/tickers/{ticker.upper()}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
            timeout=10,
        )
        if not resp.ok:
            print(f"[warn] Ticker overview returned {resp.status_code} for {ticker} — market cap gate skipped.")
            return None
        data = resp.json()
        mcap = (data.get("results") or {}).get("market_cap")
        if mcap:
            return float(mcap)
        print(f"[warn] market_cap field missing in ticker overview response for {ticker} — market cap gate skipped.")
    except Exception as e:
        print(f"[warn] Exception fetching market cap for {ticker}: {e} — market cap gate skipped.")
    return None


# ─── API ─────────────────────────────────────────────────────────────────────

def fetch_transactions(ticker: str, from_date: str, to_date: str, limit: int) -> list:
    params = {
        "tickers":      ticker.upper(),
        "limit":        limit,
        "sort":         "filing_date.desc",
        "record_type":  "transaction",
    }
    if from_date:
        params["filing_date.gte"] = from_date
    if to_date:
        params["filing_date.lte"] = to_date

    resp = requests.get(
        BASE_URL + ENDPOINT,
        params=params,
        headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
        timeout=30,
    )

    if not resp.ok:
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or resp.text
        except Exception:
            msg = resp.text
        sys.exit(f"API error {resp.status_code}: {msg}")

    # Uncomment to inspect raw response:
    # import json; print(json.dumps(resp.json(), indent=2)); sys.exit()

    return resp.json().get("results") or []


# ─── SIGNAL ENGINE ───────────────────────────────────────────────────────────

def compute_signal(transactions: list, ticker: str) -> dict:
    bull_score = bear_score = 0.0
    buy_count = sell_count = 0
    buy_value = sell_value = 0.0
    buyers, sellers = set(), set()

    for tx in transactions:
        code   = tx.get("transaction_code") or ""
        shares = float(tx.get("transaction_shares") or 0)
        price  = float(tx.get("transaction_price_per_share") or 0)
        value  = float(tx.get("transaction_value") or 0) or (shares * price)
        name   = tx.get("owner_name") or "Unknown"
        title  = tx.get("officer_title") or ""
        if not title:
            if tx.get("is_director"):            title = "Director"
            elif tx.get("is_ten_percent_owner"): title = "10%"
        wt = role_weight(title)

        if code in BULLISH_CODES:
            bull_score += shares * wt + (math.log10(value + 1) * wt * 500 if value > 0 else 0)
            buy_count  += 1
            buy_value  += value
            buyers.add(name)
        elif code in BEARISH_CODES:
            raw = shares * wt + (math.log10(value + 1) * wt * 500 if value > 0 else 0)
            bear_score += raw * SELL_DISCOUNT
            sell_count += 1
            sell_value += value
            sellers.add(name)

    # Cluster buy bonus
    if len(buyers) >= 3:
        bull_score *= 1.3

    total     = bull_score + bear_score
    net_ratio = (bull_score - bear_score) / total if total > 0 else 0.0
    total_txns = len(transactions)

    # ── No open-market activity at all ──
    if buy_count == 0 and sell_count == 0:
        return {
            "signal": "NEUTRAL",
            "reason": (f"No open-market transactions in {total_txns} filing row(s). "
                       "All activity is compensation-related (grants, exercises, withholdings)."),
        }

    # ── BULLISH ──
    if net_ratio > BULLISH_THRESHOLD:
        cluster = f" Cluster buying across {len(buyers)} distinct insiders." if len(buyers) >= 3 else ""
        return {
            "signal": "BULLISH",
            "reason": (f"{buy_count} open-market purchase(s) totalling {fmt_usd(buy_value)} "
                       f"vs {sell_count} sale(s) ({fmt_usd(sell_value)}).{cluster}"),
        }

    # ── BEARISH — requires all three gates to pass ──
    if net_ratio < BEARISH_THRESHOLD and buy_count == 0:

        # Gate 1: need enough distinct sellers — a single insider's routine
        # selling plan should not by itself produce a bearish label.
        if len(sellers) < BEARISH_MIN_SELLERS:
            return {
                "signal": "NEUTRAL",
                "reason": (f"{sell_count} sale(s) totalling {fmt_usd(sell_value)} "
                           f"from only {len(sellers)} insider(s) — too few sellers "
                           "to distinguish coordinated concern from routine liquidation."),
            }

        # Gate 2: normalise sell value against market cap.
        # Even large absolute dollar amounts are immaterial at mega-cap companies.
        market_cap = fetch_market_cap(ticker)
        if market_cap and market_cap > 0:
            fraction = sell_value / market_cap
            #print(f"[debug] market cap: {fmt_usd(market_cap)} | sell value: {fmt_usd(sell_value)} | fraction: {fraction*100:.4f}% | threshold: {BEARISH_MIN_MCAP_FRACTION*100:.4f}%")
            if fraction < BEARISH_MIN_MCAP_FRACTION:
                return {
                    "signal": "NEUTRAL",
                    "reason": (f"{sell_count} sale(s) totalling {fmt_usd(sell_value)} "
                               f"({fraction*100:.4f}% of ~{fmt_usd(market_cap)} market cap) — "
                               "immaterial relative to company size."),
                }
        else:
            print(f"[debug] market cap unavailable — market cap gate skipped, proceeding to BEARISH.")

        # All gates passed — genuinely unusual selling
        return {
            "signal": "BEARISH",
            "reason": (f"Unusually concentrated selling: {sell_count} sale(s) totalling "
                       f"{fmt_usd(sell_value)} across {len(sellers)} insiders, "
                       f"zero offsetting purchases. "
                       f"Sellers: {', '.join(list(sellers)[:3])}."),
        }

    # ── NEUTRAL catch-all ──
    if buy_count == 0:
        return {
            "signal": "NEUTRAL",
            "reason": (f"{sell_count} sale(s) totalling {fmt_usd(sell_value)}, "
                       "but insider selling alone is not a reliable negative signal."),
        }

    return {
        "signal": "NEUTRAL",
        "reason": (f"Mixed activity: {buy_count} purchase(s) ({fmt_usd(buy_value)}) "
                   f"vs {sell_count} sale(s) ({fmt_usd(sell_value)}). "
                   "No strong directional conviction."),
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Form 4 filings from Massive.com and output a bullish/bearish/neutral signal."
    )
    parser.add_argument("--ticker",    required=True, help="Stock ticker, e.g. AAPL")
    parser.add_argument("--from-date", default=None,  help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--to-date",   default=None,  help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--days",      type=int, default=90,  help="Lookback window in days (default: 90)")
    parser.add_argument("--limit",     type=int, default=100, help="Max transactions to fetch (default: 100)")
    return parser.parse_args()


def main():
    if API_KEY == "YOUR_API_KEY_HERE":
        sys.exit("Please set your API key in the API_KEY variable at the top of the script.")

    args      = parse_args()
    to_date   = args.to_date   or date.today().isoformat()
    from_date = args.from_date or (date.today() - timedelta(days=args.days)).isoformat()

    transactions = fetch_transactions(args.ticker, from_date, to_date, args.limit)
    result       = compute_signal(transactions, args.ticker)

    print(f"Signal: {result['signal']}")
    print(f"Reason: {result['reason']}")


if __name__ == "__main__":
    main()
