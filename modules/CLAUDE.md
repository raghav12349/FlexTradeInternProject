# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a multi-module stock analysis project built by an intern team. Each team
member owns one or more Python modules in `modules/`. `app.py` at the project
root is the intended integration entry point. The shared GitHub repo is
**public**: `https://github.com/raghav12349/FlexTradeInternProject` (branch `main`).

## ⚠️ API key / secrets status (READ FIRST)

The premium key `UPTtLEsTavIccF5ESguZSdtWW3zX93WW` is **hardcoded and publicly
committed** in `modules/anshu2.py` and `modules/justin.py` (committed by
teammates, live on `origin/main`). Because the repo is public, **this key is
leaked and should be rotated.** The owner (Aarav) has stated he accepts API
exposure for *local-only* use, but a public push is a different risk class — he
chose env-var handling specifically to keep the key off GitHub for his own files.

Current key handling per module (working tree):

| Module | Key source |
|---|---|
| aarav.py, aarav2.py, aarav3.py, aarav4.py, anshu.py | `os.environ["POLYGON_API_KEY"]` — **fail-fast if unset** (no fallback) |
| diya.py, samar.py | `os.environ.get("MASSIVE_API_KEY")` |
| cosmo.py | placeholder `"ENTER API HERE"` |
| **anshu2.py, justin.py** | **hardcoded premium key — PUBLIC LEAK, rotate** |

Do not re-introduce hardcoded keys into the aarav modules. Set the key locally:

```bash
export POLYGON_API_KEY=your_key_here     # aarav*, anshu
export MASSIVE_API_KEY=your_key_here     # diya, samar
```

## Environment / runtime gotcha

- `massive` is **not installed** anywhere on this machine. Every module that does
  `try: from massive import RESTClient / except ImportError: from polygon import
  RESTClient` runs on the **polygon fallback** (`polygon-api-client`).
- The user's terminal uses **Homebrew Python 3.14.5** (`/opt/homebrew/bin/python3`),
  not pyenv 3.12.4. `polygon-api-client` is installed in all three interpreters
  (pyenv 3.12.4, system 3.9.6, Homebrew 3.14.5). If a fresh interpreter ever
  errors with `ModuleNotFoundError: No module named 'polygon'`, install with:
  `python3 -m pip install --break-system-packages polygon-api-client`.
- Polygon **free tier ≈ 5 req/min** (HTTP 429 when exceeded). The premium key has
  no such limit, so the aarav modules make direct calls with no throttling.

## Running Modules

```bash
export POLYGON_API_KEY=...           # required — modules fail fast without it
python3 modules/aarav.py  AAPL       # MACD
python3 modules/aarav2.py AAPL       # RSI  (was aarav.2.py; renamed to aarav2.py)
python3 modules/aarav3.py AAPL       # SMA
python3 modules/aarav4.py AAPL       # EMA
# All four accept comma-separated symbols and print a per-cap-tier comparison:
python3 modules/aarav4.py "AAPL, MSFT, NVDA"
```

## Architecture

- `modules/<name>.py` — self-contained per-member modules.
- All market data from the [Polygon.io REST API](https://polygon.io/docs) via the
  `massive`/`polygon` RESTClient (drop-in compatible: `get_aggs`, `get_sma`,
  `get_ema`, `get_ticker_details`; aarav2/RSI uses raw `requests` + `BASE_URL`).

### The four aarav modules (all owned by Aarav)

All four share the same scaffolding: market-cap tiering (`classify_cap` /
`CAP_TIERS`: Mega ≥$200B, Large ≥$10B, Mid ≥$2B, else Small), `fetch_daily_prices`
→ closes+volumes, a volume profile (20d vs 90d → High/Normal/Low confidence),
context (price, 200-day MA, 52-week high), a 1–10 score, and shared
`trend_label` / `buy_label` thresholds (≥8 Strong Buy, ≥6 Buy, 5 Neutral, ≥3
Sell, else Strong Sell). Multi-symbol runs print a `print_comparison` table
ranked within each cap tier.

- **aarav.py — MACD.** Multi-timeframe MACD scoring (`score_macd`, 1–10). 200-day
  MA floor prevents labeling an uptrend pullback as a sell (floored at 4.5 when
  price > MA200). `analyze()` returns `context` / `timeframes` / `composite`.
- **aarav2.py — RSI** (formerly `aarav.2.py`). Trader-grade RSI with 5 weighted
  components: percentile rank, divergence, regime, momentum, failure swings.
  Uses raw `requests` against `BASE_URL` (not the RESTClient).
- **aarav3.py — SMA.** Trader-grade SMA over windows (20, 50, 200) via
  `client.get_sma()`. 4 weighted components: **stack alignment 35%**
  (price>20>50>200), **crossover 25%** (golden/death cross of SMA50 over SMA200),
  **slope 20%** (least-squares SMA50 trend), **extension 20%** (stretch from
  SMA20). Volume-confidence nudge on the raw score.
- **aarav4.py — EMA.** Same 4-component framework as aarav3 but via
  `client.get_ema()` over (20, 50, 200). Identical weights; reacts faster than
  SMA (earlier signal, more whipsaw, tempered by the volume nudge). Public API:
  `fetch_ema_series`, `analyze_ema`, `analyze`, `print_result`, `print_comparison`.

Shared public surface (aarav3/aarav4):
```python
prices, volumes = fetch_daily_prices(symbol, start, end)
ema_map = {w: fetch_ema_series(symbol, w) for w in EMA_WINDOWS}   # aarav4
result  = analyze(symbol, prices, volumes, ticker_details, ema_map=ema_map)
print_result(result)
```

### Other team modules

`aarav5.py` (untracked/new), `anshu.py` (dividend factor), `anshu2.py` (short
interest), `cosmo.py` (insider), `diya.py` (liquidity), `justin.py` (financial
ratios), `samar.py` (momentum), `kavin.py`. Integrated into the app via adapters;
each signal is shown in its owner's native scale.

## Known cosmetic issue

All four aarav modules use `datetime.utcfromtimestamp(...)` which emits a
`DeprecationWarning` on Python 3.12+. Harmless. Modernize to
`datetime.fromtimestamp(ts, datetime.UTC)` across all four if desired (offered,
not yet applied).
