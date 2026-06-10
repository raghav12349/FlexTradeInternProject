"""Run every registered module across tickers and assemble the results.

Everything user-facing is on a common 1-10 scale (`ten`). Numeric signals carry
a `ten`; qualitative ones (e.g. cosmo's BULLISH/BEARISH) carry ten=None and are
shown as labels. The composite is the mean of the available numeric `ten`s —
the same scale the individual signals use — not an opaque normalized decimal.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd

from core.registry import load_signals
from core.scoring import fmt_ten, ten_to_label, to_ten


def _breakdown(result: dict) -> list[str]:
    bd = result.get("breakdown")
    if bd:
        return list(bd)
    det = result.get("details") or {}
    if "note" in det:
        return [str(det["note"])]
    return [f"{k}: {v}" for k, v in det.items()] or ["(no breakdown provided)"]


def _err_signal(owner: str, exc) -> dict:
    err = f"ERR:{exc.__class__.__name__}"
    return {"owner": owner, "ten": None, "native_score": "—",
            "native_rating": err, "breakdown": [str(exc)]}


def analyze_ticker(ticker: str, period: str = "2y") -> dict:
    """Run all modules for one ticker. Each signal carries `ten` (1-10 or None),
    `native_score` (display), `native_rating` (author's own label), `breakdown`.
    The composite is the mean of the numeric `ten`s on the same 1-10 scale."""
    signals: dict[str, dict] = {}

    for entry in load_signals():
        name, owner = entry["name"], entry["owner"]
        if entry["error"] is not None:
            signals[name] = _err_signal(owner, entry["error"])
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                if entry["adapter"]:
                    result = entry["adapter"]["analyze"](entry["module"], ticker, period)
                else:
                    result = entry["module"].analyze(ticker, period=period)

            # Adapters already provide `ten`. Contract modules (placeholders)
            # return a [-1, 1] `score`; convert it onto the 1-10 scale.
            if "ten" in result:
                ten = result["ten"]
            else:
                ten = to_ten(result.get("score"), -1.0, 1.0)
            native_score = result.get("native_score") or fmt_ten(ten)
            native_rating = result.get("native_rating") or ten_to_label(ten)
            signals[name] = {
                "owner": owner,
                "ten": ten,
                "native_score": native_score,
                "native_rating": native_rating,
                "breakdown": _breakdown(result),
            }
        except NotImplementedError:
            signals[name] = {"owner": owner, "ten": None, "native_score": "—",
                             "native_rating": "N/A", "breakdown": ["not implemented yet"]}
        # Some modules call sys.exit() on API errors (SystemExit is not an
        # Exception) — catch it too so one module can't kill the whole run.
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            signals[name] = _err_signal(owner, exc)

    tens = [s["ten"] for s in signals.values() if isinstance(s["ten"], (int, float))]
    composite = round(sum(tens) / len(tens), 1) if tens else None

    return {
        "ticker": ticker.upper(),
        "signals": signals,
        "composite": composite,                 # 1-10 (or None)
        "composite_label": ten_to_label(composite),
        "n_scored": len(tens),
    }


def run(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Wide table: ticker index, one column per signal (1-10), plus composite."""
    rows = []
    for ticker in tickers:
        report = analyze_ticker(ticker, period=period)
        row: dict = {"ticker": report["ticker"]}
        for name, sig in report["signals"].items():
            row[name] = sig["ten"]
        row["composite_1_10"] = report["composite"]
        row["rating"] = report["composite_label"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker")


def export_csv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return path
