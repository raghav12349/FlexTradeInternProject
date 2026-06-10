# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a multi-module stock analysis project built by an intern team. Each team member owns one Python module in `modules/`. The entry point is `app.py` at the project root.

## Running Modules

```bash
# Run aarav.py (MACD analysis) — interactive mode
python modules/aarav.py

# Run with CLI args: symbol and optional timeframe
python modules/aarav.py AAPL
python modules/aarav.py TSLA 3M
```

## Environment

Set `POLYGON_API_KEY` in your environment to use your own Polygon.io API key. Modules fall back to a hardcoded key if unset.

```bash
export POLYGON_API_KEY=your_key_here
```

## Architecture

- `modules/<name>.py` — one module per team member; each module is self-contained
- `app.py` — root entry point (currently a stub) intended to compose/integrate the modules
- All market data is fetched from the [Polygon.io REST API](https://polygon.io/docs)

### aarav.py — MACD Stock Analysis

Core public API:

```python
from modules.aarav import fetch_daily_prices, analyze, print_result

prices = fetch_daily_prices(symbol, start_date, end_date)  # → {date_str: close_price}
result = analyze(symbol, prices, timeframe)                 # timeframe: "1M","3M","6M","1Y","2Y","ALL"
print_result(result)
```

`analyze()` returns a structured dict:
- `context` — current price, 200-day MA, 52-week high
- `timeframes` — list of per-timeframe MACD scores and recommendations
- `composite` — averaged score, overall trend, and buy/sell recommendation

**Scoring:** `score_macd()` produces a 1–10 score. Scores ≥8 = Strong Buy, ≥6 = Buy, 5 = Neutral, ≤3 = Sell/Strong Sell. A 200-day MA floor prevents labeling a pullback in an uptrend as a sell (score floored at 4.5 when price > MA200).

### Other modules

`cosmo.py`, `samar.py`, `kavin.py`, `diya.py`, `anshu.py`, `justin.py` are currently empty stubs to be implemented by each team member.
