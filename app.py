"""FlexTrade equity research bot.

Runs all 7 per-person signals on a ticker and combines them into a composite.

Interactive (enter tickers one at a time):
    python app.py

Batch (analyse a list, print a table, export CSV):
    python app.py AAPL NVDA MSFT TSLA --period 2y --csv data/ratings.csv

Rank a universe best-to-long / best-to-short (exports data/rankings.csv):
    python app.py AAPL NVDA MSFT TSLA --rank
    python app.py --universe TECH --rank

NOTE: every signal is currently a PLACEHOLDER returning an arbitrary score.
Each owner replaces the body of their modules/<name>.py to plug in real logic.
"""
from __future__ import annotations

import argparse

from core.env import load_local_keys
from core.recommender import rank
from core.runner import analyze_ticker, export_csv, run
from core.scoring import is_scored
from core.universe import resolve


def print_report(report: dict) -> None:
    """Pretty-print one ticker's per-signal ratings, then how each was computed."""
    print(f"\n=== {report['ticker']} ===")
    print(f"{'signal':<18}{'score':>10}   rating")
    print("-" * 50)
    for name, sig in report["signals"].items():
        print(f"{name:<18}{sig['native_score']:>10}   {sig['rating']}")
    print("-" * 50)
    comp = report["composite"]
    comp_str = f"{comp:.1f}/10" if is_scored(comp) else "—"
    print(f"{'COMPOSITE (avg of ' + str(report['n_scored']) + ' signals)':<25}{comp_str:>10}   {report['composite_label']}")

    print("\n── How each rating was computed ──")
    for name, sig in report["signals"].items():
        print(f"\n[{name}] → {sig['rating']}  (author's own: {sig['native_rating']})")
        for line in sig["breakdown"]:
            print(f"   • {line}")


def interactive(period: str) -> None:
    print("FlexTrade research bot — enter a ticker (or 'quit').")
    while True:
        try:
            raw = input("\nticker> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        if raw.lower() in {"q", "quit", "exit"}:
            break
        for ticker in raw.replace(",", " ").split():
            print_report(analyze_ticker(ticker, period=period))


def main() -> None:
    load_local_keys()  # pick up POLYGON_API_KEY / MASSIVE_API_KEY from .keys.env if present
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="Tickers to analyse. Omit for interactive mode.")
    parser.add_argument("--universe", help="Named universe/sector (e.g. MEGACAP, TECH) instead of listing tickers")
    parser.add_argument("--rank", action="store_true", help="Rank by composite and label Long/Neutral/Short")
    parser.add_argument("--period", default="2y", help="Lookback window, e.g. 1y, 2y, 5y (default: %(default)s)")
    parser.add_argument("--csv", default="data/ratings.csv", help="Output CSV path (default: %(default)s)")
    args = parser.parse_args()

    tickers = resolve(args.universe) if args.universe else args.tickers

    if not tickers:
        interactive(args.period)
        return

    if args.rank:
        df = rank(tickers, period=args.period)
        print(df.to_string())
        path = export_csv(df, "data/rankings.csv")
        print(f"\nwrote {path}")
        return

    # A single ticker gets the per-signal breakdown then composite; multiple
    # tickers get the wide comparison table. Both export a CSV.
    if len(tickers) == 1:
        print_report(analyze_ticker(tickers[0], period=args.period))

    df = run(tickers, period=args.period)
    if len(tickers) > 1:
        print(df.to_string())
    path = export_csv(df, args.csv)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
