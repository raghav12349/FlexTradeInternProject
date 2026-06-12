"""How high can accuracy go, and what's the average growth?

(1) Average forward GROWTH (total return) by rating bucket, 6m/1y/2y.
(2) Compare composite recipes to push market-relative accuracy as high as
    possible: current weights vs. dropping the signals that hurt vs. IC-weighted
    vs. momentum+trend only vs. flipping RSI, and a strict (top-decile-only) BUY.

Technical signals only (~5y price history available). Survivorship-biased
(current S&P 500), so absolute growth is generous — judge the SPREADS.
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

CAP, HORIZONS, EVERY, MINN = 280, {"6m": 126, "1y": 252, "2y": 504}, 21, 40


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


# composite recipes: weight per factor (negative = flip direction; 0 = drop)
RECIPES = {
    "current (house weights)":   {"momentum": .17, "macd": .151, "rsi": .17, "sma": .226, "ema": .189, "sma_crossover": .094},
    "drop rsi+crossover":        {"momentum": .30, "macd": .15, "sma": .35, "ema": .20},
    "IC-weighted":               {"momentum": .105, "sma": .090, "ema": .035, "sma_crossover": .020},
    "momentum + trend only":     {"momentum": .5, "sma": .5},
    "flip RSI to trend":         {"momentum": .17, "macd": .151, "rsi": -.17, "sma": .226, "ema": .189, "sma_crossover": .094},
}


def composite(factors, dt, weights):
    comp = None
    for n, w in weights.items():
        if n not in factors or w == 0:
            continue
        z = zscore_xs(factors[n].loc[dt]) * w
        comp = z if comp is None else comp.add(z, fill_value=0)
    return None if comp is None else comp.dropna()


def run():
    print("Fetching ~6y panel…")
    close = fetch()
    print(f"  {close.shape[1]} tickers x {close.shape[0]} days ({close.index[0].date()}→{close.index[-1].date()})")
    factors = build_factors(close)
    fwd = {h: close.shift(-d) / close - 1 for h, d in HORIZONS.items()}
    dates = close.index[252:len(close) - 126:EVERY]

    # ── (1) average GROWTH by bucket (current recipe) ──
    print("\n" + "=" * 72)
    print("AVERAGE GROWTH (total return over the hold) — current composite")
    print("=" * 72)
    buck = {h: {k: [] for k in ("market", "top10", "top20", "buy", "sell", "bot10")} for h in HORIZONS}
    for dt in dates:
        comp = composite(factors, dt, RECIPES["current (house weights)"])
        if comp is None or len(comp) < MINN:
            continue
        ten = 1 + comp.rank(pct=True) * 9
        for h in HORIZONS:
            fr = fwd[h].loc[dt].reindex(ten.index)
            df = pd.DataFrame({"ten": ten, "fr": fr}).dropna()
            if len(df) < MINN:
                continue
            df = df.sort_values("ten"); n = len(df)
            b = buck[h]
            b["market"].append(df["fr"].mean())
            b["top10"].append(df["fr"].iloc[-n // 10:].mean())
            b["top20"].append(df["fr"].iloc[-n // 5:].mean())
            b["bot10"].append(df["fr"].iloc[:n // 10].mean())
            b["buy"].append(df["fr"][df["ten"] >= 6.5].mean())
            b["sell"].append(df["fr"][df["ten"] < 4.5].mean())
    print(f"  {'hold':<5}{'market':>9}{'BUY(>=6.5)':>11}{'top 20%':>9}{'top 10%':>9}{'SELL(<4.5)':>11}{'bottom10':>10}")
    for h in HORIZONS:
        m = {k: np.nanmean(v) * 100 for k, v in buck[h].items()}
        print(f"  {h:<5}{m['market']:>8.1f}%{m['buy']:>10.1f}%{m['top20']:>8.1f}%{m['top10']:>8.1f}%{m['sell']:>10.1f}%{m['bot10']:>9.1f}%")

    # ── (2) accuracy by recipe ──
    print("\n" + "=" * 72)
    print("MARKET-RELATIVE ACCURACY by composite recipe  (higher = better)")
    print("  overall = (BUY beat mkt + SELL lagged mkt) / all buy+sell calls")
    print("  strict  = only the TOP 10% rated as BUY: how often they beat mkt")
    print("=" * 72)
    for name, wts in RECIPES.items():
        out = {}
        for h in HORIZONS:
            bn = bb = sn = sl = tn = tb = 0
            for dt in dates:
                comp = composite(factors, dt, wts)
                if comp is None or len(comp) < MINN:
                    continue
                ten = 1 + comp.rank(pct=True) * 9
                fr = fwd[h].loc[dt].reindex(ten.index)
                df = pd.DataFrame({"ten": ten, "fr": fr}).dropna()
                if len(df) < MINN:
                    continue
                exc = df["fr"] - df["fr"].mean()
                buy, sell = df["ten"] >= 6.5, df["ten"] < 4.5
                top = df["ten"] >= df["ten"].quantile(0.9)
                bn += buy.sum(); bb += (exc[buy] > 0).sum()
                sn += sell.sum(); sl += (exc[sell] < 0).sum()
                tn += top.sum(); tb += (exc[top] > 0).sum()
            overall = (bb + sl) / (bn + sn) * 100 if (bn + sn) else np.nan
            strict = tb / tn * 100 if tn else np.nan
            out[h] = (overall, strict)
        cells = "   ".join(f"{h}: {out[h][0]:.0f}%/{out[h][1]:.0f}%" for h in HORIZONS)
        print(f"  {name:<26} {cells}")
    print("\n  (format = overall%/top-10%-BUY% per horizon)")


if __name__ == "__main__":
    run()
