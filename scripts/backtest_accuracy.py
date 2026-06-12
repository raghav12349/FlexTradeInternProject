"""Accuracy backtest: per-call BUY/SELL accuracy AND cross-sectional ranking,
over 6-month / 1-year / 2-year / 5-year holds, on the S&P 500.

Two notions of "accuracy", both reported as percentages:
  • RAW directional   — when rated BUY, did the stock simply rise? (inflated by
                        the market's long-run drift, shown for context only)
  • MARKET-RELATIVE   — when rated BUY, did it BEAT the average stock that day?
                        when rated SELL, did it LAG? (this isolates real skill)

Only the price/technical signals are testable point-in-time (~53% of composite
weight); fundamentals/sentiment can't be reconstructed historically.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for _l in pathlib.Path(".keys.env").read_text().splitlines():
    if "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ[_k.strip()] = _v.strip()

from core.massive import aggregates          # noqa: E402
from core.universe import resolve            # noqa: E402
from core.weights import weight_for          # noqa: E402
from scripts.backtest_signals import build_factors, zscore_xs  # noqa: E402

UNIVERSE_CAP = 250
YEARS = 15
HORIZONS = {"6m": 126, "1y": 252, "2y": 504, "5y": 1260}
REBALANCE_EVERY = 21
MIN_NAMES = 40


def fetch_panel():
    tickers = resolve("SP500")[:UNIVERSE_CAP]
    start = (date.today() - timedelta(days=int(365.25 * YEARS))).isoformat()
    end = date.today().isoformat()

    def _one(tk):
        try:
            bars = aggregates(tk, from_=start, to=end)
            if len(bars) < 500:
                return tk, None
            return tk, pd.Series({pd.Timestamp(b["t"], unit="ms").normalize(): b["c"] for b in bars})
        except Exception:
            return tk, None

    series, t0 = {}, time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(_one, tk) for tk in tickers]):
            tk, s = f.result()
            if s is not None:
                series[tk] = s
    panel = pd.DataFrame(series).sort_index()
    panel = panel[panel.index.dayofweek < 5].dropna(how="all")
    print(f"  fetched {panel.shape[1]} tickers x {panel.shape[0]} days "
          f"({panel.index[0].date()}→{panel.index[-1].date()}) in {time.time()-t0:.0f}s")
    return panel


def composite_scores(close):
    """Per-date composite as a cross-sectional 1-10 (percentile of weighted z)."""
    factors = build_factors(close)
    w = {n: weight_for(n) for n in factors}
    ws = sum(w.values())
    w = {k: v / ws for k, v in w.items()}
    return factors, w


def run():
    print(f"Fetching {YEARS}y price panel (S&P 500)…")
    close = fetch_panel()
    factors, w = composite_scores(close)
    fwd = {h: close.shift(-d) / close - 1 for h, d in HORIZONS.items()}
    rows = close.index
    dates = rows[252:len(rows) - 126:REBALANCE_EVERY]

    # accumulate per-horizon pooled observations
    ic = {h: [] for h in HORIZONS}
    buckets = {h: {"buy_n": 0, "buy_up": 0, "buy_beat": 0,
                   "sell_n": 0, "sell_dn": 0, "sell_lag": 0,
                   "exc_by_q": [[] for _ in range(5)]} for h in HORIZONS}

    for dt in dates:
        comp = None
        for n, fac in factors.items():
            z = zscore_xs(fac.loc[dt])
            comp = z * w[n] if comp is None else comp.add(z * w[n], fill_value=0)
        if comp is None or comp.dropna().shape[0] < MIN_NAMES:
            continue
        comp = comp.dropna()
        ten = 1 + comp.rank(pct=True) * 9            # 1-10, like the system
        for h, d in HORIZONS.items():
            fr = fwd[h].loc[dt].reindex(ten.index)
            df = pd.DataFrame({"ten": ten, "fr": fr}).dropna()
            if len(df) < MIN_NAMES:
                continue
            mkt = df["fr"].mean()
            df["exc"] = df["fr"] - mkt
            ic[h].append(df["ten"].rank().corr(df["fr"].rank()))
            buy = df[df["ten"] >= 6.5]                # BUY / STRONG BUY
            sell = df[df["ten"] < 4.5]                # SELL / STRONG SELL
            b = buckets[h]
            b["buy_n"] += len(buy);  b["buy_up"] += int((buy["fr"] > 0).sum());  b["buy_beat"] += int((buy["exc"] > 0).sum())
            b["sell_n"] += len(sell); b["sell_dn"] += int((sell["fr"] < 0).sum()); b["sell_lag"] += int((sell["exc"] < 0).sum())
            q = pd.qcut(df["ten"].rank(method="first"), 5, labels=False)
            for i in range(5):
                b["exc_by_q"][i].append(df["exc"][q == i].mean())

    print("\n" + "=" * 78)
    print("RANKING ACCURACY  —  mean Information Coefficient (rank corr vs fwd return)")
    print("=" * 78)
    print(f"  {'hold':<6}{'mean IC':>10}{'hit% of periods':>18}{'# periods':>12}")
    for h in HORIZONS:
        a = np.array([x for x in ic[h] if not np.isnan(x)])
        print(f"  {h:<6}{a.mean():>10.3f}{(a>0).mean()*100:>16.0f}%{len(a):>12}")

    print("\n" + "=" * 78)
    print("SINGLE-TICKER BUY/SELL ACCURACY  (pooled across all stocks & dates)")
    print("=" * 78)
    print(f"  {'hold':<6}| {'BUY: went up':>13} {'BUY: beat mkt':>14} | "
          f"{'SELL: went down':>16} {'SELL: lagged mkt':>17} | {'overall vs-mkt':>15}")
    for h in HORIZONS:
        b = buckets[h]
        buy_up = b["buy_up"] / b["buy_n"] * 100 if b["buy_n"] else float("nan")
        buy_beat = b["buy_beat"] / b["buy_n"] * 100 if b["buy_n"] else float("nan")
        sell_dn = b["sell_dn"] / b["sell_n"] * 100 if b["sell_n"] else float("nan")
        sell_lag = b["sell_lag"] / b["sell_n"] * 100 if b["sell_n"] else float("nan")
        overall = ((b["buy_beat"] + b["sell_lag"]) / (b["buy_n"] + b["sell_n"]) * 100
                   if (b["buy_n"] + b["sell_n"]) else float("nan"))
        print(f"  {h:<6}| {buy_up:>12.0f}% {buy_beat:>13.0f}% | "
              f"{sell_dn:>15.0f}% {sell_lag:>16.0f}% | {overall:>14.0f}%")
        print(f"  {'':<6}  (BUY calls n={b['buy_n']:,}, SELL calls n={b['sell_n']:,})")

    print("\n" + "=" * 78)
    print("AVG MARKET-RELATIVE RETURN BY RATING QUINTILE (low→high score)")
    print("  monotone increasing = the score orders outcomes correctly")
    print("=" * 78)
    for h in HORIZONS:
        qs = [np.nanmean(buckets[h]["exc_by_q"][i]) * 100 for i in range(5)]
        print(f"  {h:<6}: " + "  ".join(f"{q:+5.1f}%" for q in qs))


if __name__ == "__main__":
    run()
