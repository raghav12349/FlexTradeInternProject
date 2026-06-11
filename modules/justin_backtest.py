"""
justin_backtest.py

Backtest the financial-ratios scoring module (modules/justin.py).

Two modes
---------
  current   (default)
      Uses the latest available ratios from list_financials_ratios.
      Simple but has look-ahead bias — ratios reflect today's stock
      price, not the stock price on the buy date.

  historical  (--mode historical)
      Re-builds ratios from the annual income statement + balance sheet
      that were publicly available *as of BUY_DATE*, combined with the
      actual stock price on that date.  No look-ahead bias.

Why ADBE was a big loss (current mode)
---------------------------------------
  In "current" mode, price-dependent ratios like P/E and EV/Sales are
  computed using today's stock price.  If ADBE's price has fallen since
  BUY_DATE, today's P/E looks *cheaper* than it actually was back then,
  making the current score artificially high.  The model then labels it
  a "buy" — but the signal was already priced in (or wrong).  "Historical"
  mode avoids this by using the price on BUY_DATE to compute those ratios.

  Additionally, financial ratios score fundamentals, not market sentiment.
  Adobe has faced headwinds from generative-AI competition (Canva, Firefly
  adoption ceiling, Midjourney) and multiple compression in high-P/E tech.
  A strong balance sheet doesn't protect against narrative-driven selling.

Usage
-----
  python modules/justin_backtest.py
  python modules/justin_backtest.py --mode historical
  python modules/justin_backtest.py --seed 42 --threshold 7.5
  python modules/justin_backtest.py --ticker ADBE          # single stock
"""

from __future__ import annotations

import sys
import os
import random
import argparse
import time
from datetime import date, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from massive import RESTClient
from modules.justin import (
    rate_stock, API_KEY,
    _fetch_sector, _compute_score, SECTOR_PROFILES, DEFAULT_PROFILE,
    _PCT_FIELDS,
)
from core.universe import UNIVERSES

BUY_DATE  = date(2025, 12, 11)   # ~6 months ago from 2026-06-11
TODAY     = date(2026, 6, 11)
THRESHOLD = 8.0


# ── Price helper ─────────────────────────────────────────────────────────────

def _get_close(client: RESTClient, ticker: str, target: date) -> Optional[float]:
    """Closing price on or after target (first trading day within 7 days)."""
    for offset in range(7):
        d = target + timedelta(days=offset)
        if d > TODAY:
            break
        try:
            bars = client.get_aggs(
                ticker, 1, "day",
                from_=d.isoformat(),
                to=(d + timedelta(days=1)).isoformat(),
                limit=1,
            )
            if bars:
                return bars[0].close
        except Exception:
            pass
    return None


# ── Historical ratio reconstruction ──────────────────────────────────────────

def _fetch_historical_ratios(
    client: RESTClient,
    ticker: str,
    as_of: date,
    price: float,
) -> dict[str, float]:
    """
    Reconstruct financial ratios from filings available as of `as_of`.
    Uses the actual stock `price` on that date, so there is no look-ahead
    bias from price-dependent ratios (P/E, EV/Sales, EV/EBITDA, P/B).
    """
    ratios: dict[str, float] = {}

    # ── Income statements (need 2 years for revenue growth) ──────────────────
    inc_rows = list(client.list_financials_income_statements(
        tickers=ticker,
        timeframe="annual",
        period_end_lte=as_of.isoformat(),
        limit=2,
    ))
    inc_rows.sort(key=lambda r: r.period_end, reverse=True)
    if not inc_rows:
        return ratios
    inc      = inc_rows[0]
    inc_prev = inc_rows[1] if len(inc_rows) > 1 else None

    # ── Balance sheet ─────────────────────────────────────────────────────────
    bs_rows = list(client.list_financials_balance_sheets(
        tickers=ticker,
        timeframe="annual",
        period_end_lte=as_of.isoformat(),
        limit=1,
    ))
    bs = bs_rows[0] if bs_rows else None

    # ── Derived values ────────────────────────────────────────────────────────
    revenue  = getattr(inc, "revenue",                              None)
    net_inc  = getattr(inc, "consolidated_net_income_loss",         None)
    ebitda   = getattr(inc, "ebitda",                               None)
    eps_dil  = getattr(inc, "diluted_earnings_per_share",           None)
    shares   = (getattr(inc, "diluted_shares_outstanding",  None) or
                getattr(inc, "basic_shares_outstanding",    None))

    equity        = None
    total_debt    = 0.0
    curr_assets   = None
    curr_liabs    = None
    cash          = 0.0
    inventories   = 0.0
    total_assets  = None

    if bs:
        equity      = (getattr(bs, "total_equity",                        None) or
                       getattr(bs, "total_equity_attributable_to_parent", None))
        total_assets = getattr(bs, "total_assets",                         None)
        curr_assets  = getattr(bs, "total_current_assets",                 None)
        curr_liabs   = getattr(bs, "total_current_liabilities",            None)
        cash         = (getattr(bs, "cash_and_equivalents",                None) or 0)
        inv          = getattr(bs, "inventories",                          None)
        inventories  = inv or 0
        debt_c       = getattr(bs, "debt_current",                         None) or 0
        debt_lt      = getattr(bs, "long_term_debt_and_capital_lease_obligations", None) or 0
        total_debt   = debt_c + debt_lt

    # ── Compute each ratio ────────────────────────────────────────────────────

    # P/E
    if eps_dil and eps_dil > 0:
        ratios["price_to_earnings"] = price / eps_dil

    # Revenue growth (YoY annual)
    rev_prev = getattr(inc_prev, "revenue", None) if inc_prev else None
    if revenue is not None and rev_prev is not None and rev_prev != 0:
        ratios["revenue_growth"] = (revenue - rev_prev) / abs(rev_prev) * 100

    # ROE  (stored as decimal % like the API; * 100 applied in _compute_score)
    if net_inc is not None and equity and equity > 0:
        ratios["return_on_equity"] = (net_inc / equity) * 100

    # ROA
    if net_inc is not None and total_assets and total_assets > 0:
        ratios["return_on_assets"] = (net_inc / total_assets) * 100

    # D/E
    if equity and equity > 0:
        ratios["debt_to_equity"] = total_debt / equity

    # Current ratio
    if curr_assets and curr_liabs and curr_liabs > 0:
        ratios["current"] = curr_assets / curr_liabs

    # Quick ratio
    if curr_assets and curr_liabs and curr_liabs > 0:
        ratios["quick"] = (curr_assets - inventories) / curr_liabs

    # P/B  (price / book value per share)
    if shares and shares > 0 and equity and equity > 0:
        bvps = equity / shares
        ratios["price_to_book"] = price / bvps

    # EV-based ratios require market cap
    if shares and shares > 0 and revenue and revenue > 0:
        mktcap = price * shares
        ev     = mktcap + total_debt - cash
        ratios["ev_to_sales"] = ev / revenue
        if ebitda and ebitda > 0:
            ratios["ev_to_ebitda"] = ev / ebitda

    # Dividend yield: not computable from inc/bs alone; skip (will be missing)

    return ratios


def _score_historical(
    client: RESTClient,
    ticker: str,
    as_of: date,
) -> Optional[dict]:
    """
    Score a ticker using financial data available as of `as_of`.
    Returns the same dict shape as rate_stock(), or None on failure.
    """
    price = _get_close(client, ticker, as_of)
    if price is None:
        return None

    sector  = _fetch_sector(client, ticker)
    profile = SECTOR_PROFILES.get(sector, DEFAULT_PROFILE)
    ratios  = _fetch_historical_ratios(client, ticker, as_of, price)

    if not ratios:
        return None

    score, breakdown = _compute_score(ratios, profile)
    return {
        "ticker":    ticker.upper(),
        "sector":    sector,
        "score":     score,
        "breakdown": breakdown,
    }


# ── Universe helper ───────────────────────────────────────────────────────────

def _all_tickers() -> list[str]:
    seen: set[str] = set()
    out:  list[str] = []
    for tickers in UNIVERSES.values():
        for t in tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


# ── Main backtest ─────────────────────────────────────────────────────────────

def run(
    n:         int   = 40,
    seed:      int | None = None,
    threshold: float = THRESHOLD,
    mode:      str   = "current",
    ticker_only: str | None = None,
) -> None:
    client = RESTClient(API_KEY)

    if ticker_only:
        sample = [ticker_only.upper()]
    else:
        rng      = random.Random(seed)
        universe = _all_tickers()
        sample   = rng.sample(universe, min(n, len(universe)))

    print(f"Mode: {mode}  |  buy threshold: {threshold}/10")
    print(f"Buy date: {BUY_DATE}   Current date: {TODAY}")
    print()

    buys:    list[dict] = []
    skipped: int        = 0

    for ticker in sample:
        print(f"  {ticker:<6}", end=" ", flush=True)
        try:
            if mode == "historical":
                rating = _score_historical(client, ticker, BUY_DATE)
                if rating is None:
                    print("(no historical data)")
                    skipped += 1
                    continue
            else:
                rating = rate_stock(ticker, api_key=API_KEY)

            score  = rating["score"]
            sector = rating["sector"]
            print(f"{score:4.1f}/10  {sector}", end="")

            if score > threshold:
                print("  → BUY", end=" ", flush=True)
                buy_px = (
                    _get_close(client, ticker, BUY_DATE)
                    if mode == "current"
                    else rating.get("_buy_price")          # already fetched
                      or _get_close(client, ticker, BUY_DATE)
                )

                cur_px: Optional[float] = None
                for delta in range(1, 5):
                    cur_px = _get_close(client, ticker, TODAY - timedelta(days=delta))
                    if cur_px:
                        break

                if buy_px and cur_px:
                    ret = (cur_px - buy_px) / buy_px * 100
                    buys.append({
                        "ticker":      ticker,
                        "sector":      sector,
                        "score":       score,
                        "buy_price":   buy_px,
                        "cur_price":   cur_px,
                        "return_pct":  ret,
                        "breakdown":   rating.get("breakdown", {}),
                    })
                    print(f"${buy_px:.2f} → ${cur_px:.2f}  ({ret:+.1f}%)")
                else:
                    print("(price data unavailable)")
            else:
                print()

        except Exception as e:
            print(f"  ERROR: {e}")
            skipped += 1

        time.sleep(0.12)

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    if not buys:
        print(f"No stocks exceeded the {threshold}/10 threshold in {mode} mode.")
        return

    buys.sort(key=lambda r: r["return_pct"], reverse=True)
    w = 68
    print("=" * w)
    print(f"  BACKTEST RESULTS [{mode.upper()}]  |  {BUY_DATE} → {TODAY}")
    print("=" * w)
    print(f"{'Ticker':<7} {'Sector':<26} {'Score':>5}  {'Buy':>8}  {'Now':>8}  {'Return':>8}")
    print("-" * w)
    for r in buys:
        print(f"{r['ticker']:<7} {r['sector']:<26} {r['score']:>5.1f}  "
              f"${r['buy_price']:>7.2f}  ${r['cur_price']:>7.2f}  {r['return_pct']:>+7.1f}%")

    avg  = sum(r["return_pct"] for r in buys) / len(buys)
    wins = sum(1 for r in buys if r["return_pct"] > 0)
    print("-" * w)
    print(f"  Positions: {len(buys)}   Wins: {wins}/{len(buys)}   Avg return: {avg:+.1f}%")
    if skipped:
        print(f"  ({skipped} ticker(s) skipped due to missing data or errors)")
    print()

    # Detail breakdown for any losing position whose score was high
    losers = [r for r in buys if r["return_pct"] < -5]
    if losers and not ticker_only:
        print("Notable losses — metric breakdown:")
        for r in losers:
            print(f"\n  {r['ticker']}  score={r['score']}  return={r['return_pct']:+.1f}%")
            for m, info in r["breakdown"].items():
                print(f"    {m:<25} value={info['value']:>9.3f}  score={info['score']:>4.1f}/10")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest justin.py financial-ratios scorer")
    ap.add_argument("--mode",      choices=["current", "historical"], default="current",
                    help="'current' uses latest ratios (fast, look-ahead bias); "
                         "'historical' rebuilds ratios from filings as of BUY_DATE (accurate)")
    ap.add_argument("--seed",      type=int,   default=None)
    ap.add_argument("--n",         type=int,   default=40,   help="Stocks to sample (default 40)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help="Buy threshold (default 8.0)")
    ap.add_argument("--ticker",    type=str,   default=None, help="Diagnose a single ticker")
    args = ap.parse_args()
    run(n=args.n, seed=args.seed, threshold=args.threshold,
        mode=args.mode, ticker_only=args.ticker)
