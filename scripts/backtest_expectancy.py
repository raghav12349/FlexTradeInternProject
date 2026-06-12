"""Trade expectancy / profitability of the composite's BUY calls.

For each call (a stock rated BUY when the score fires), over 6m/1y/2y holds:
  win rate, average WIN, average LOSS, payoff ratio, and EXPECTANCY
  (= win% x avgWin - loss% x avgLoss = expected return per call).

Reported two ways:
  ABSOLUTE   — actual long P&L: a "win" = the stock went up.
  VS MARKET  — alpha: a "win" = the stock beat the average stock that day.

Technical signals only, ~5y price history, current S&P 500 (survivorship-biased
=> absolute numbers are generous; the vs-market figures are the honest edge).
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for _l in pathlib.Path(".keys.env").read_text().splitlines():
    if "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ[_k.strip()] = _v.strip()

from core.massive import aggregates          # noqa: E402
from core.universe import resolve            # noqa: E402
from scripts.backtest_signals import build_factors, zscore_xs  # noqa: E402

CAP, HORIZONS, EVERY, MINN = 250, {"6m": 126, "1y": 252, "2y": 504}, 21, 40
WEIGHTS = {"momentum": .17, "macd": .151, "rsi": .17, "sma": .226, "ema": .189, "sma_crossover": .094}


def fetch():
    tk = resolve("SP500")[:CAP]
    start = (date.today() - timedelta(days=365 * 6)).isoformat()
    end = date.today().isoformat()

    def one(t):
        try:
            b = aggregates(t, from_=start, to=end)
            return (t, pd.Series({pd.Timestamp(x["t"], unit="ms").normalize(): x["c"] for x in b})) if len(b) > 400 else (t, None)
        except Exception:
            return t, None
    ser = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(one, t) for t in tk]):
            t, s = f.result()
            if s is not None:
                ser[t] = s
    p = pd.DataFrame(ser).sort_index()
    return p[p.index.dayofweek < 5].dropna(how="all")


def expectancy(vals: np.ndarray):
    vals = vals[~np.isnan(vals)]
    if len(vals) == 0:
        return None
    wins, losses = vals[vals > 0], vals[vals <= 0]
    wr = len(wins) / len(vals)
    aw = wins.mean() if len(wins) else 0.0
    al = losses.mean() if len(losses) else 0.0          # negative
    exp = vals.mean()                                   # = wr*aw + (1-wr)*al
    payoff = (aw / abs(al)) if al != 0 else float("inf")
    return dict(n=len(vals), wr=wr, aw=aw, al=al, exp=exp, payoff=payoff)


def run():
    print("Fetching ~6y panel…")
    close = fetch()
    print(f"  {close.shape[1]} tickers x {close.shape[0]} days ({close.index[0].date()}→{close.index[-1].date()})")
    factors = build_factors(close)
    fwd = {h: close.shift(-d) / close - 1 for h, d in HORIZONS.items()}
    dates = close.index[252:len(close) - 126:EVERY]

    abs_ret = {h: {"buy": [], "top10": [], "mkt": []} for h in HORIZONS}
    exc_ret = {h: {"buy": [], "top10": []} for h in HORIZONS}

    for dt in dates:
        comp = None
        for n, w in WEIGHTS.items():
            z = zscore_xs(factors[n].loc[dt]) * w
            comp = z if comp is None else comp.add(z, fill_value=0)
        if comp is None or len(comp.dropna()) < MINN:
            continue
        comp = comp.dropna()
        ten = 1 + comp.rank(pct=True) * 9
        for h in HORIZONS:
            fr = fwd[h].loc[dt].reindex(ten.index)
            df = pd.DataFrame({"ten": ten, "fr": fr}).dropna()
            if len(df) < MINN:
                continue
            mkt = df["fr"].mean()
            buy = df[df["ten"] >= 6.5]
            top = df[df["ten"] >= df["ten"].quantile(0.9)]
            abs_ret[h]["buy"] += list(buy["fr"]);   exc_ret[h]["buy"] += list(buy["fr"] - mkt)
            abs_ret[h]["top10"] += list(top["fr"]); exc_ret[h]["top10"] += list(top["fr"] - mkt)
            abs_ret[h]["mkt"] += list(df["fr"])

    def show(title, getter):
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)
        print(f"  {'bucket':<8}{'hold':<5}{'win%':>7}{'avg WIN':>10}{'avg LOSS':>10}{'payoff':>8}{'EXPECTANCY':>12}{'n':>8}")
        for label, key in (("BUY", "buy"), ("TOP10%", "top10"), ("market", "mkt")):
            for h in HORIZONS:
                arr = getter(h, key)
                if arr is None:
                    continue
                e = expectancy(np.array(arr))
                if e is None:
                    continue
                print(f"  {label:<8}{h:<5}{e['wr']*100:>6.0f}%{e['aw']*100:>9.1f}%{e['al']*100:>9.1f}%"
                      f"{e['payoff']:>8.2f}{e['exp']*100:>11.1f}%{e['n']:>8,}")

    show("ABSOLUTE P&L  (win = stock went up; expectancy = expected return per BUY call)",
         lambda h, k: abs_ret[h].get(k))
    show("VS MARKET / ALPHA  (win = stock beat the average stock; expectancy = excess per call)",
         lambda h, k: exc_ret[h].get(k) if k in ("buy", "top10") else None)


if __name__ == "__main__":
    run()
