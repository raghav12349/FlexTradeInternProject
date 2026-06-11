"""Run every registered module across tickers and assemble the results.

Signals run concurrently (they're I/O-bound API calls), so a full analysis takes
about as long as the single slowest signal rather than the sum. Results are
cached per (ticker, period). Everything user-facing is on a 1-10 scale (`ten`);
insider labels (cosmo) map to fixed 1-10 anchors. The composite is the mean of
the available numeric `ten`s.
"""
from __future__ import annotations

import contextlib
import io
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import pandas as pd

from core.registry import load_signals
from core.scoring import fmt_ten, is_scored, ten_to_label, to_ten

_SIGNAL_TIMEOUT = 45      # seconds per signal before it's marked timed-out
_CACHE: dict[tuple, dict] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _breakdown(result: dict) -> list[str]:
    bd = result.get("breakdown")
    if bd:
        return list(bd)
    det = result.get("details") or {}
    if "note" in det:
        return [str(det["note"])]
    return [f"{k}: {v}" for k, v in det.items()] or ["(no breakdown provided)"]


def _err_signal(owner: str, label: str, msg: str) -> dict:
    return {"owner": owner, "ten": None, "native_score": "—",
            "native_rating": label, "breakdown": [msg]}


def _run_one(entry: dict, ticker: str, period: str) -> dict:
    """Run a single signal and normalize it to the common shape."""
    owner = entry["owner"]
    if entry["error"] is not None:
        return _err_signal(owner, f"ERR:{entry['error'].__class__.__name__}", str(entry["error"]))
    try:
        if entry["adapter"]:
            result = entry["adapter"]["analyze"](entry["module"], ticker, period)
        else:
            result = entry["module"].analyze(ticker, period=period)
        ten = result["ten"] if "ten" in result else to_ten(result.get("score"), -1.0, 1.0)
        if not is_scored(ten):
            ten = None
        return {
            "owner": owner,
            "ten": ten,
            "native_score": result.get("native_score") or fmt_ten(ten),
            "native_rating": result.get("native_rating") or ten_to_label(ten),
            "breakdown": _breakdown(result),
        }
    except NotImplementedError:
        return _err_signal(owner, "N/A", "not implemented yet")
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - incl. modules that sys.exit()
        return _err_signal(owner, f"ERR:{exc.__class__.__name__}", str(exc))


def analyze_ticker(ticker: str, period: str = "2y", use_cache: bool = True) -> dict:
    """Run all signals for one ticker (concurrently). Cached per (ticker, period)."""
    cache_key = (ticker.upper(), period)
    if use_cache and cache_key in _CACHE:
        return _CACHE[cache_key]

    entries = load_signals()
    signals: dict[str, dict] = {}

    # Suppress module stdout and run signals in parallel. redirect_stdout is
    # global, so it wraps the whole concurrent section.
    with contextlib.redirect_stdout(io.StringIO()):
        ex = ThreadPoolExecutor(max_workers=min(10, len(entries)))
        futures = {e["name"]: ex.submit(_run_one, e, ticker, period) for e in entries}
        for e in entries:
            try:
                signals[e["name"]] = futures[e["name"]].result(timeout=_SIGNAL_TIMEOUT)
            except FutureTimeout:
                signals[e["name"]] = _err_signal(e["owner"], "timeout", "signal timed out")
        ex.shutdown(wait=False, cancel_futures=True)

    tens = [s["ten"] for s in signals.values() if is_scored(s["ten"])]
    composite = round(sum(tens) / len(tens), 1) if tens else None
    report = {
        "ticker": ticker.upper(),
        "signals": signals,
        "composite": composite,
        "composite_label": ten_to_label(composite),
        "n_scored": len(tens),
    }
    _CACHE[cache_key] = report
    return report


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
