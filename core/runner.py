"""Run every registered module across tickers and assemble the results."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.rating import score_to_rating
from core.registry import load_signals


def _breakdown(result: dict) -> list[str]:
    """The reasoning lines for a signal: prefer the module's own breakdown,
    else fall back to its details, so every signal has something to show."""
    bd = result.get("breakdown")
    if bd:
        return list(bd)
    det = result.get("details") or {}
    if "note" in det:
        return [str(det["note"])]
    return [f"{k}: {v}" for k, v in det.items()] or ["(no breakdown provided)"]


def analyze_ticker(ticker: str, period: str = "2y") -> dict:
    """Run all modules for one ticker.

    Returns:
        {
          "ticker": "AAPL",
          "signals": {
              "momentum_3_6_12": {
                  "owner": "samar", "score": 0.42, "rating": "Buy",
                  "native_rating": "Buy", "breakdown": [...]
              }, ...
          },
          "composite": 0.08,            # equal-weight mean of available scores
          "composite_rating": "Hold",
        }

    `rating` is our normalized label (drives nothing user-facing); `native_rating`
    is the module's OWN label and is what the UI shows. `breakdown` explains how
    that rating was computed, in the module's own logic.
    """
    signals: dict[str, dict] = {}

    for entry in load_signals():
        name, owner = entry["name"], entry["owner"]
        # A module that failed to import (e.g. missing dependency) shows an
        # error in its column instead of crashing the whole run.
        if entry["error"] is not None:
            err = f"ERR:{entry['error'].__class__.__name__}"
            signals[name] = {"owner": owner, "score": None, "rating": err,
                             "native_rating": err, "breakdown": [str(entry["error"])]}
            continue
        # Pluggable per-person code — isolate failures too.
        try:
            if entry["adapter"]:
                result = entry["adapter"]["analyze"](entry["module"], ticker, period)
            else:
                result = entry["module"].analyze(ticker, period=period)
            signals[name] = {
                "owner": owner,
                "score": result.get("score"),
                "rating": result.get("rating"),
                "native_rating": result.get("native_rating") or result.get("rating"),
                "breakdown": _breakdown(result),
            }
        except NotImplementedError:
            signals[name] = {"owner": owner, "score": None, "rating": "N/A",
                             "native_rating": "N/A", "breakdown": ["not implemented yet"]}
        except Exception as exc:  # noqa: BLE001
            err = f"ERR:{exc.__class__.__name__}"
            signals[name] = {"owner": owner, "score": None, "rating": err,
                             "native_rating": err, "breakdown": [str(exc)]}

    # Composite: simple equal-weight mean of whatever scored. Swap this for a
    # weighted / rank-based scheme once everyone's signal is finalised.
    scores = [s["score"] for s in signals.values() if isinstance(s["score"], (int, float))]
    composite = round(sum(scores) / len(scores), 3) if scores else None

    return {
        "ticker": ticker.upper(),
        "signals": signals,
        "composite": composite,
        "composite_rating": score_to_rating(composite) if composite is not None else "N/A",
    }


def run(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Wide table: ticker index, one column per signal score, plus composite."""
    rows = []
    for ticker in tickers:
        report = analyze_ticker(ticker, period=period)
        row: dict = {"ticker": report["ticker"]}
        for name, sig in report["signals"].items():
            row[name] = sig["score"]
        row["composite"] = report["composite"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker")


def export_csv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return path
