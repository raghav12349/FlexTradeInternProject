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
import json
import math
import sys
from datetime import date, timedelta

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: run  pip3 install requests")

# ─── HARD-CODE YOUR API KEY HERE ─────────────────────────────────────────────
API_KEY = "ENTER API HERE"
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
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n/1_000:.1f}K"
    return f"${n:.2f}"


# ─── API ─────────────────────────────────────────────────────────────────────

def fetch_transactions(ticker: str, from_date: str, to_date: str, limit: int) -> list:
    """
    Fetches Form 4 data from Massive. The response is a flat list —
    each result is one transaction row with fields like:
      transaction_code, transaction_shares, transaction_price_per_share,
      transaction_value, owner_name, officer_title, filing_date, etc.
    """
    params = {
        "tickers":          ticker.upper(),
        "limit":            limit,
        "sort":             "filing_date.desc",
        # Only fetch transaction rows, not holding-only rows
        "record_type":      "transaction",
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

    data = resp.json()

    # Uncomment to inspect the raw response and verify fields:
    # print(json.dumps(data, indent=2)); sys.exit()

    return data.get("results") or []


# ─── SIGNAL ENGINE ───────────────────────────────────────────────────────────

def compute_signal(transactions: list) -> dict:
    bull_score = bear_score = 0.0
    buy_count = sell_count = 0
    buy_value = sell_value = 0.0
    buyers, sellers = set(), set()

    for tx in transactions:
        code   = tx.get("transaction_code") or ""
        # Use pre-computed transaction_value if available, else calculate
        shares = float(tx.get("transaction_shares") or 0)
        price  = float(tx.get("transaction_price_per_share") or 0)
        value  = float(tx.get("transaction_value") or 0) or (shares * price)
        name   = tx.get("owner_name") or "Unknown"
        title  = tx.get("officer_title") or ""
        # Supplement title from role flags when officer_title is absent
        if not title:
            if tx.get("is_director"):   title = "Director"
            elif tx.get("is_ten_percent_owner"): title = "10%"
        wt = role_weight(title)

        if code in BULLISH_CODES:
            bull_score += shares * wt + (math.log10(value + 1) * wt * 500 if value > 0 else 0)
            buy_count  += 1
            buy_value  += value
            buyers.add(name)
        elif code in BEARISH_CODES:
            bear_score += shares * wt + (math.log10(value + 1) * wt * 500 if value > 0 else 0)
            sell_count += 1
            sell_value += value
            sellers.add(name)
        # All other codes (A, M, F, G, etc.) are compensation/noise — ignored

    # Cluster bonus: 3+ distinct insiders buying is historically a strong signal
    if len(buyers) >= 3:
        bull_score *= 1.3
    if len(sellers) >= 3:
        bear_score *= 1.1

    total     = bull_score + bear_score
    net_ratio = (bull_score - bear_score) / total if total > 0 else 0.0

    total_txns = len(transactions)

    if buy_count == 0 and sell_count == 0:
        signal = "NEUTRAL"
        reason = (f"No open-market transactions in {total_txns} filing row(s). "
                  "All activity is compensation-related (grants, option exercises, tax withholdings).")
    elif net_ratio > 0.25:
        cluster = f" Cluster buying across {len(buyers)} distinct insiders." if len(buyers) >= 3 else ""
        signal  = "BULLISH"
        reason  = (f"{buy_count} open-market purchase(s) totalling {fmt_usd(buy_value)} "
                   f"vs {sell_count} open-market sale(s) ({fmt_usd(sell_value)}).{cluster}")
    elif net_ratio < -0.25:
        signal = "BEARISH"
        reason = (f"{sell_count} open-market sale(s) totalling {fmt_usd(sell_value)} dominate "
                  f"vs {buy_count} purchase(s) ({fmt_usd(buy_value)}). "
                  f"Sellers: {', '.join(list(sellers)[:3])}.")
    else:
        signal = "NEUTRAL"
        reason = (f"Mixed signals: {buy_count} purchase(s) ({fmt_usd(buy_value)}) "
                  f"vs {sell_count} sale(s) ({fmt_usd(sell_value)}). "
                  "Insufficient conviction either way.")

    return {"signal": signal, "reason": reason}


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
    result       = compute_signal(transactions)

    print(f"Signal: {result['signal']}")
    print(f"Reason: {result['reason']}")


if __name__ == "__main__":
    main()
