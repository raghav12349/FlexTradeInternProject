"""Cross-sectional ranking on the 1-10 composite scale.

Given a list of tickers, run every signal, take the 1-10 composite (the same
scale used for individual signals — not an opaque normalized decimal), then
rank highest-to-lowest. Top/bottom buckets get Long / Short labels.
"""
from __future__ import annotations

import pandas as pd

from core.runner import analyze_ticker
from core.scoring import is_scored, ten_to_label


def _labels(values: list, long_frac: float, short_frac: float) -> list[str]:
    n = len(values)
    if n == 0:
        return []
    n_long = max(1, round(n * long_frac))
    n_short = max(1, round(n * short_frac))
    out = []
    for i, v in enumerate(values):
        if not is_scored(v):
            out.append("N/A")
        elif i < n_long:
            out.append("Long")
        elif i >= n - n_short:
            out.append("Short")
        else:
            out.append("Neutral")
    return out


def rank(tickers: list[str], period: str = "2y",
         long_frac: float = 0.3, short_frac: float = 0.3) -> pd.DataFrame:
    """DataFrame ranked by the 1-10 composite, with rank + rating + recommendation.

    Columns: rank, <each signal's 1-10>, composite_1_10, rating, recommendation.
    Index: ticker. Sorted best-to-long (rank 1) down to best-to-short.
    """
    rows = []
    for ticker in tickers:
        report = analyze_ticker(ticker, period=period)
        row: dict = {"ticker": report["ticker"]}
        for name, sig in report["signals"].items():
            row[name] = sig["ten"]
        row["composite_1_10"] = report["composite"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("ticker").sort_values("composite_1_10", ascending=False, na_position="last")
    df.insert(0, "rank", range(1, len(df) + 1))
    df["rating"] = [ten_to_label(v) if is_scored(v) else "N/A" for v in df["composite_1_10"]]
    df["recommendation"] = _labels(df["composite_1_10"].tolist(), long_frac, short_frac)
    return df
