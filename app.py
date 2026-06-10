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
from core.universe import resolve


def print_report(report: dict) -> None:
    """Pretty-print one ticker's per-signal ratings, then how each was computed."""
    print(f"\n=== {report['ticker']} ===")
    print(f"{'signal':<18}{'owner':<9}{'their score':>12}   their rating")
    print("-" * 62)
    for name, sig in report["signals"].items():
        print(f"{name:<18}{sig['owner']:<9}{sig['native_score']:>12}   {sig['native_rating']}")
    print("-" * 62)
    comp = report["composite"]
    comp_str = f"{comp:+.3f}" if isinstance(comp, (int, float)) else "   --"
    print(f"{'COMPOSITE (blended -1..+1)':<29}{comp_str:>10}   {report['composite_rating']}")

    print("\n── How each rating was computed ──")
    for name, sig in report["signals"].items():
        print(f"\n[{sig['owner']} · {name}] → {sig['native_rating']}")
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
