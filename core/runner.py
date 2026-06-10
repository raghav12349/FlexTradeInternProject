"""Run every registered module across tickers and assemble the results."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.adapters import ADAPTERS
from core.rating import score_to_rating
from core.registry import load_modules, module_meta


def analyze_ticker(ticker: str, period: str = "2y") -> dict:
    """Run all modules for one ticker.

    Returns:
        {
          "ticker": "AAPL",
          "signals": {
              "momentum_3_6_12": {"owner": "samar", "score": 0.42, "rating": "Buy"},
              ...
          },
          "composite": 0.08,            # equal-weight mean of available scores
          "composite_rating": "Hold",
        }
    """
    modules = load_modules()
    signals: dict[str, dict] = {}

    for mod in modules:
        meta = module_meta(mod)
        name, owner = meta["name"], meta["owner"]
        adapter = ADAPTERS.get(meta["module"])
        # Pluggable per-person code — isolate failures so one bad module
        # doesn't take down the whole run.
        try:
            if adapter:
                result = adapter["analyze"](mod, ticker, period)
            else:
                result = mod.analyze(ticker, period=period)
            signals[name] = {
                "owner": owner,
                "score": result.get("score"),
                "rating": result.get("rating"),
            }
        except NotImplementedError:
            signals[name] = {"owner": owner, "score": None, "rating": "N/A"}
        except Exception as exc:  # noqa: BLE001
            signals[name] = {"owner": owner, "score": None, "rating": f"ERR:{exc.__class__.__name__}"}

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
