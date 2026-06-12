"""Point-in-time backtest of the composite stock-picker on the diverse 110-stock
universe (modules.samar.DIVERSE_UNIVERSE: 10 stocks x 11 GICS sectors), at
3-month / 6-month / 2-year holds.

HONESTY BOUNDARIES (read before trusting any number):

  * Only the PRICE/TECHNICAL signals of the composite are reconstructable
    point-in-time (momentum, macd, rsi, sma, ema, sma_crossover). The 6
    fundamental/sentiment signals (ratios, dividends, liquidity, insider, news,
    short_interest) only expose as-of-today data via the API, so they CANNOT be
    backtested without look-ahead. The technical block is ~53% of composite
    weight. This script tests that block, weighted by the real module weights,
    renormalised over the 6 -- the closest honest proxy for the live picker.

  * Massive returns only ~5 years of daily history. A 2-year hold therefore has
    room for ~1-2 NON-overlapping windows. The 2y figures below reflect
    essentially ONE market episode and are reported with that warning. 3m and 6m
    have many more independent windows.

  * DIVERSE_UNIVERSE is today's survivors (large/mid caps that still exist), so
    ABSOLUTE returns are upward biased (survivorship). The VS-UNIVERSE (alpha)
    figures cancel that bias and are the honest measure of selection skill.

Method: at monthly rebalance dates, build each factor from prices up to that date
(bullish direction matching the modules), z-score cross-sectionally, weight into a
composite, then measure realised forward returns of the stocks the composite
ranks highest. Picks = top quintile (22 names) and top decile (11 names).
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

for _line in pathlib.Path(".keys.env").read_text().splitlines():
    if "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ[_k.strip()] = _v.strip()

from core.massive import aggregates          # noqa: E402
from core.weights import weight_for          # noqa: E402
import modules.samar as samar                # noqa: E402

UNIVERSE = list(samar.DIVERSE_UNIVERSE)      # 110 names, 11 sectors x 10
YEARS = 11                                   # request max; API caps ~5y
HORIZONS = {"3m": 63, "6m": 126, "2y": 504}  # trading days
REBALANCE_EVERY = 21
MIN_NAMES = 40
TECH_FACTORS = ["momentum", "macd", "rsi", "sma", "ema", "sma_crossover"]


def fetch_panel():
    start = (date.today() - timedelta(days=int(365.25 * YEARS))).isoformat()
    end = date.today().isoformat()

    def _one(tk):
        try:
            bars = aggregates(tk, from_=start, to=end)
            if len(bars) < 260:
                return tk, None
            s = pd.Series({pd.Timestamp(b["t"], unit="ms").normalize(): b["c"] for b in bars})
            return tk, s
        except Exception:
            return tk, None

    series = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(_one, tk) for tk in UNIVERSE]):
            tk, s = f.result()
            if s is not None:
                series[tk] = s
    panel = pd.DataFrame(series).sort_index()
    panel = panel[panel.index.dayofweek < 5].dropna(how="all")
    span = (panel.index[-1] - panel.index[0]).days / 365.25
    print(f"  fetched {panel.shape[1]}/{len(UNIVERSE)} tickers x {panel.shape[0]} "
          f"trading days ({span:.1f}y: {panel.index[0].date()} -> {panel.index[-1].date()}) "
          f"in {time.time()-t0:.0f}s")
    return panel


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
        "momentum": ((close.shift(21) / close.shift(252) - 1)
                     + (close.shift(21) / close.shift(126) - 1)
                     + (close.shift(21) / close.shift(63) - 1)) / 3,
        "macd": macd_hist,
        "rsi": 50 - rsi,                    # low RSI (oversold) = bullish
        "sma": close / sma200 - 1,
        "ema": close / ema50 - 1,
        "sma_crossover": (sma20 - sma50) / sma50,
    }


def zscore_xs(row: pd.Series) -> pd.Series:
    r = row.dropna()
    if len(r) < MIN_NAMES:
        return pd.Series(dtype=float)
    z = (r - r.mean()) / (r.std() or 1.0)
    return z.clip(-3, 3)


def run():
    print("Fetching price panel (DIVERSE_UNIVERSE 110, max history)...")
    close = fetch_panel()
    factors = build_factors(close)
    fwd = {h: close.shift(-d) / close - 1 for h, d in HORIZONS.items()}

    weights = {n: weight_for(n) for n in TECH_FACTORS}
    wsum = sum(weights.values())
    weights = {k: v / wsum for k, v in weights.items()}
    print(f"  technical-block weights (renorm to 1): "
          f"{ {k: round(v,3) for k,v in weights.items()} }")

    rows = close.index
    # all rebalance dates with >=252d lookback; per-horizon we keep only those
    # that also have a full forward window (handled by dropna on fwd return).
    dates = rows[252:len(rows):REBALANCE_EVERY]

    # accumulators per horizon
    pick_q = {h: [] for h in HORIZONS}      # top-quintile pick forward returns (absolute)
    pick_q_rel = {h: [] for h in HORIZONS}  # ...minus that date's universe mean (alpha)
    pick_d = {h: [] for h in HORIZONS}      # top-decile pick forward returns (absolute)
    pick_d_rel = {h: [] for h in HORIZONS}
    bot_q = {h: [] for h in HORIZONS}       # bottom-quintile (for long-short spread)
    uni_mean = {h: [] for h in HORIZONS}    # equal-weight universe forward return (benchmark)
    ndates = {h: 0 for h in HORIZONS}
    date_used = {h: [] for h in HORIZONS}

    for dt in dates:
        comp = None
        for name, fac in factors.items():
            z = zscore_xs(fac.loc[dt])
            if z.empty:
                comp = None
                break
            comp = z * weights[name] if comp is None else comp.add(z * weights[name], fill_value=0)
        if comp is None:
            continue
        for h in HORIZONS:
            fr = fwd[h].loc[dt]
            d = pd.concat([comp, fr], axis=1).dropna()
            if len(d) < 50:
                continue
            d.columns = ["score", "ret"]
            d = d.sort_values("score")
            n = len(d)
            umean = d["ret"].mean()
            top_q = d["ret"].iloc[-(n // 5):]
            top_d = d["ret"].iloc[-(n // 10):]
            bottom_q = d["ret"].iloc[:n // 5]
            pick_q[h].extend(top_q.tolist())
            pick_q_rel[h].extend((top_q - umean).tolist())
            pick_d[h].extend(top_d.tolist())
            pick_d_rel[h].extend((top_d - umean).tolist())
            bot_q[h].append(bottom_q.mean())
            uni_mean[h].append(umean)
            ndates[h] += 1
            date_used[h].append(dt)

    def prof(rets):
        a = np.array(rets, float)
        a = a[~np.isnan(a)]
        if len(a) == 0:
            return None
        wins = a[a > 0]
        losses = a[a <= 0]
        win_rate = len(wins) / len(a)
        avg_win = wins.mean() if len(wins) else 0.0
        avg_loss = losses.mean() if len(losses) else 0.0  # negative
        payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("nan")
        expect = a.mean()
        return dict(n=len(a), mean=a.mean(), median=float(np.median(a)),
                    win=win_rate, avg_win=avg_win, avg_loss=avg_loss,
                    payoff=payoff, expect=expect)

    print("\n" + "=" * 78)
    print("COMPOSITE STOCK-PICKER BACKTEST  -  diverse 110 universe (technical block, ~53% wt)")
    print("=" * 78)

    for h, dlen in HORIZONS.items():
        ann = 252 / dlen
        q = prof(pick_q[h]); qr = prof(pick_q_rel[h]); dd = prof(pick_d[h])
        if q is None:
            print(f"\n--- {h} hold: no data ---")
            continue
        # independence estimate: span of used dates / horizon length
        used = date_used[h]
        if used:
            span_days = (used[-1] - used[0]).days
            indep = max(1, round(span_days / (dlen / 252 * 365.25)) + 1)
        else:
            indep = 0
        umean = np.nanmean(uni_mean[h])
        botq = np.nanmean(bot_q[h])
        print(f"\n{'='*78}\n  HOLD = {h}  ({dlen} trading days, x{ann:.2f} to annualize)")
        print(f"  rebalance dates with a full forward window: {ndates[h]}"
              f"   (~{indep} independent {h} windows)")
        print(f"  date range of entries: {used[0].date()} -> {used[-1].date()}")
        print("  " + "-" * 74)
        print(f"  BENCHMARK  equal-weight 110 universe avg {h} return : {umean*100:+6.2f}%"
              f"   ({(umean*ann)*100:+6.1f}% ann.)")
        print("  " + "-" * 74)
        print(f"  TOP-QUINTILE PICKS (22 names/date, {q['n']} pick-instances):")
        print(f"     average GROWTH (total return)   : {q['mean']*100:+6.2f}% per {h}"
              f"   ({q['mean']*ann*100:+6.1f}% annualized)")
        print(f"     median growth                   : {q['median']*100:+6.2f}%")
        print(f"     WIN RATE (absolute, ret>0)      : {q['win']*100:5.1f}%")
        print(f"     WIN RATE (vs universe, alpha>0) : {qr['win']*100:5.1f}%")
        print(f"     avg win / avg loss              : {q['avg_win']*100:+.2f}% / {q['avg_loss']*100:+.2f}%")
        print(f"     payoff ratio                    : {q['payoff']:.2f}")
        print(f"     EXPECTANCY per pick (absolute)  : {q['expect']*100:+6.2f}% per {h}")
        print(f"     EXPECTANCY per pick (vs market) : {qr['mean']*100:+6.2f}% per {h}  <-- honest edge")
        print(f"  TOP-DECILE PICKS (11 names/date, {dd['n']} pick-instances):")
        print(f"     average growth                  : {dd['mean']*100:+6.2f}% per {h}"
              f"   ({dd['mean']*ann*100:+6.1f}% ann.),  win rate {dd['win']*100:.1f}%")
        print(f"  LONG-SHORT  top-quintile minus bottom-quintile  : "
              f"{(q['mean']-botq)*100:+6.2f}% per {h}  ({(q['mean']-botq)*ann*100:+6.1f}% ann.)")

    print("\n" + "=" * 78)
    print("READ-ME ON THESE NUMBERS:")
    print("  * Absolute growth/win-rate are survivorship-inflated (universe = today's")
    print("    survivors). The 'vs market / alpha' rows remove that bias -- judge skill there.")
    print("  * 2y rests on ~1-2 independent windows = one market episode. Not significant.")
    print("  * Covers the technical ~53% of the composite only; fundamentals not testable.")
    print("=" * 78)


if __name__ == "__main__":
    run()
