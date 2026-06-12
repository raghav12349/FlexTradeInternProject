"""Point-in-time backtest of the PRICE/TECHNICAL signals + a weighted technical
composite, across the S&P 500 over ~5 years.

Why only the technical signals: the fundamental/sentiment signals (ratios,
dividends, liquidity, insider, news, short_interest) only expose as-of-today data
via the APIs, so they can't be reconstructed point-in-time without look-ahead.
The technical signals can be computed from historical prices alone, so they're
backtestable honestly. They carry ~53% of the composite weight.

Method: at monthly rebalance dates, compute each signal from data up to that date
(in its bullish direction, matching the modules), then measure the cross-sectional
rank correlation (Spearman Information Coefficient) with forward returns, plus
top-vs-bottom decile forward-return spreads. No disk writes (in-memory only).
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

for _line in pathlib.Path(".keys.env").read_text().splitlines():
    if "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ[_k.strip()] = _v.strip()

from core.massive import aggregates          # noqa: E402
from core.universe import resolve            # noqa: E402
from core.weights import weight_for          # noqa: E402

UNIVERSE_CAP = 280
YEARS = 6
HORIZONS = {"1m": 21, "3m": 63, "6m": 126}
REBALANCE_EVERY = 21          # trading days between rebalances
MIN_NAMES = 40                # min cross-section per date


# ── 1. Build the close panel ────────────────────────────────────────────────
def fetch_panel():
    from datetime import date, timedelta
    tickers = resolve("SP500")[:UNIVERSE_CAP]
    start = (date.today() - timedelta(days=int(365.25 * YEARS))).isoformat()
    end = date.today().isoformat()

    def _one(tk):
        try:
            bars = aggregates(tk, from_=start, to=end)
            if len(bars) < 300:
                return tk, None
            s = pd.Series({pd.Timestamp(b["t"], unit="ms").normalize(): b["c"] for b in bars})
            return tk, s
        except Exception:
            return tk, None

    series = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_one, tk) for tk in tickers]
        for f in as_completed(futures):
            tk, s = f.result()
            if s is not None:
                series[tk] = s
    panel = pd.DataFrame(series).sort_index()
    panel = panel[panel.index.dayofweek < 5].dropna(how="all")
    print(f"  fetched {panel.shape[1]} tickers x {panel.shape[0]} days in {time.time()-t0:.0f}s")
    return panel


# ── 2. Vectorised point-in-time factors (higher = more bullish) ─────────────
def build_factors(close: pd.DataFrame):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_hist = (macd - macd.ewm(span=9, adjust=False).mean()) / close

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    sma20, sma50, sma200 = (close.rolling(w).mean() for w in (20, 50, 200))
    ema50 = close.ewm(span=50, adjust=False).mean()

    return {
        # samar: blended 12-1 / 6-1 / 3-1 momentum (skip last month)
        "momentum": ((close.shift(21) / close.shift(252) - 1)
                     + (close.shift(21) / close.shift(126) - 1)
                     + (close.shift(21) / close.shift(63) - 1)) / 3,
        "macd": macd_hist,                                  # aarav: rising/positive = bullish
        "rsi": 50 - rsi,                                    # aarav2: LOW rsi (oversold) = bullish
        "sma": close / sma200 - 1,                          # aarav3: above long MA = bullish
        "ema": close / ema50 - 1,                           # aarav4: above MA = bullish
        "sma_crossover": (sma20 - sma50) / sma50,           # kavin: fast above slow = bullish
    }


def zscore_xs(row: pd.Series) -> pd.Series:
    r = row.dropna()
    if len(r) < MIN_NAMES:
        return pd.Series(dtype=float)
    z = (r - r.mean()) / (r.std() or 1.0)
    return z.clip(-3, 3)


def spearman_ic(a: pd.Series, b: pd.Series) -> float | None:
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < MIN_NAMES:
        return None
    return df.iloc[:, 0].rank().corr(df.iloc[:, 1].rank())


# ── 3. Backtest loop ────────────────────────────────────────────────────────
def run():
    print("Fetching price panel (S&P 500, ~6y)…")
    close = fetch_panel()
    factors = build_factors(close)
    fwd = {h: close.shift(-d) / close - 1 for h, d in HORIZONS.items()}

    weights = {name: weight_for(name) for name in factors}
    wsum = sum(weights.values())
    weights = {k: v / wsum for k, v in weights.items()}

    rows = close.index
    dates = rows[252:len(rows) - max(HORIZONS.values()):REBALANCE_EVERY]
    print(f"  {len(dates)} rebalance dates; weights(renorm)="
          f"{ {k: round(v,3) for k,v in weights.items()} }")

    ic = {h: {name: [] for name in list(factors) + ["COMPOSITE"]} for h in HORIZONS}
    dec = {h: {"top": [], "bottom": []} for h in HORIZONS}   # composite deciles
    quint = {h: [[] for _ in range(5)] for h in HORIZONS}

    for dt in dates:
        comp = None
        zcache = {}
        for name, fac in factors.items():
            z = zscore_xs(fac.loc[dt])
            zcache[name] = z
            comp = z * weights[name] if comp is None else comp.add(z * weights[name], fill_value=0)
        for h in HORIZONS:
            fr = fwd[h].loc[dt]
            for name in factors:
                v = spearman_ic(zcache[name], fr)
                if v is not None:
                    ic[h][name].append(v)
            v = spearman_ic(comp, fr)
            if v is not None:
                ic[h]["COMPOSITE"].append(v)
            # decile / quintile forward returns by composite
            d = pd.concat([comp, fr], axis=1).dropna()
            if len(d) >= 50:
                d.columns = ["score", "ret"]
                d = d.sort_values("score")
                n = len(d)
                dec[h]["bottom"].append(d["ret"].iloc[:n // 10].mean())
                dec[h]["top"].append(d["ret"].iloc[-n // 10:].mean())
                for i in range(5):
                    quint[h][i].append(d["ret"].iloc[i * n // 5:(i + 1) * n // 5].mean())

    # ── report ──
    def stats(vals):
        a = np.array(vals, float)
        a = a[~np.isnan(a)]
        if len(a) == 0:
            return (np.nan, np.nan, np.nan, 0)
        mean = a.mean()
        ir = mean / (a.std() or np.nan)
        return (mean, ir, (a > 0).mean(), len(a))

    print("\n" + "=" * 74)
    print("INFORMATION COEFFICIENT  (mean Spearman rank corr of signal vs fwd return)")
    print("  IC>0.02 is generally considered a useful equity factor.")
    print("=" * 74)
    for h in HORIZONS:
        print(f"\n— forward {h} —   {'signal':<14}{'mean IC':>9}{'IC IR':>8}{'hit%':>7}{'n':>6}")
        for name in list(factors) + ["COMPOSITE"]:
            m, ir, hit, n = stats(ic[h][name])
            star = "  <<" if name == "COMPOSITE" else ""
            print(f"{'':>17}{name:<14}{m:>9.3f}{ir:>8.2f}{hit*100:>6.0f}%{n:>6}{star}")

    print("\n" + "=" * 74)
    print("COMPOSITE DECILE FORWARD RETURNS  (top 10% vs bottom 10% by composite score)")
    print("=" * 74)
    for h, d in HORIZONS.items():
        top = np.nanmean(dec[h]["top"]); bot = np.nanmean(dec[h]["bottom"])
        ann = (252 / d)
        print(f"\n— forward {h} —")
        print(f"    top decile avg : {top*100:+6.2f}%   ({top*ann*100:+6.1f}% annualized)")
        print(f"    bottom decile  : {bot*100:+6.2f}%   ({bot*ann*100:+6.1f}% annualized)")
        print(f"    long-short spread: {(top-bot)*100:+6.2f}% per {h}  ({(top-bot)*ann*100:+6.1f}% ann.)")
        qs = [np.nanmean(quint[h][i]) for i in range(5)]
        print("    quintiles (low→high score): " + "  ".join(f"{q*100:+.2f}%" for q in qs))


if __name__ == "__main__":
    run()
