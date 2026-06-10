"""Cross-sectional ranking: score a universe and label Long / Neutral / Short.

Given a list of tickers, run every signal, combine into the composite, then
rank by composite (highest = best to long, lowest = best to short). The top
and bottom buckets get Long / Short labels; the middle is Neutral.
"""
from __future__ import annotations

import math

import pandas as pd

from core.runner import analyze_ticker


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _labels(composites: list, long_frac: float, short_frac: float) -> list[str]:
    """Bucket already-sorted (descending) composites into Long/Neutral/Short."""
    n = len(composites)
    if n == 0:
        return []
    n_long = max(1, round(n * long_frac))
    n_short = max(1, round(n * short_frac))
    out = []
    for i, c in enumerate(composites):
        if not _is_num(c):
            out.append("N/A")
        elif i < n_long:
            out.append("Long")
        elif i >= n - n_short:
            out.append("Short")
        else:
            out.append("Neutral")
    return out


def rank(
    tickers: list[str],
    period: str = "2y",
    long_frac: float = 0.3,
    short_frac: float = 0.3,
) -> pd.DataFrame:
    """Return a DataFrame ranked by composite, with rank + recommendation columns.

    Columns: rank, <each signal score>, composite, recommendation.
    Index: ticker. Sorted best-to-long (rank 1) down to best-to-short.
    """
    rows = []
    for ticker in tickers:
        report = analyze_ticker(ticker, period=period)
        row: dict = {"ticker": report["ticker"]}
        for name, sig in report["signals"].items():
            row[name] = sig["score"]
        row["composite"] = report["composite"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("ticker").sort_values("composite", ascending=False, na_position="last")
    df.insert(0, "rank", range(1, len(df) + 1))
    df["recommendation"] = _labels(df["composite"].tolist(), long_frac, short_frac)
    return df
