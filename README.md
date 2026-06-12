# FlexTrade - Multi-Factor Equity Research

Each person's signal (in `modules/`) is run on a ticker, put on a common
**1–10 scale**, labelled with one shared rating vocabulary (**STRONG BUY / BUY /
HOLD / SELL / STRONG SELL**), and combined into a 1–10 composite. Surfaced three
ways: a terminal breakdown, a CSV export, and a branded desktop dashboard with
search-by-name, price/signal charts, a grouped news window, and a long/short
recommender.

📐 **[How every factor and the composite are calculated → `docs/METHODOLOGY.md`](docs/METHODOLOGY.md)**

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# API keys (Polygon + Massive). Either export them, or drop a gitignored
# .keys.env at the repo root and the app auto-loads it:
#   POLYGON_API_KEY=...
#   MASSIVE_API_KEY=...
export POLYGON_API_KEY=your_key_here
export MASSIVE_API_KEY=your_key_here
```

## Run

```bash
python app.py AAPL                 # one ticker: per-signal breakdown + composite
python app.py AAPL NVDA TSLA       # several: comparison table -> data/ratings.csv
python app.py --universe TECH --rank   # rank long->short -> data/rankings.csv
python dashboard.py                # desktop app: charts, search-by-name, recommender
```

## The signal contract

Each `modules/<name>.py` should expose:

```python
SIGNAL_NAME = "macd"            # column label
SIGNAL_OWNER = "aarav"          # your name
SIGNAL_CATEGORY = "Technicals"  # "Fundamentals" / "Technicals" / ...

def analyze(ticker: str, period: str = "2y", **_) -> dict:
    return {
        "ticker":  ticker.upper(),
        "signal":  SIGNAL_NAME,
        "score":   0.42,                 # REQUIRED: normalized to [-1, 1]
        "rating":  "Buy",                # use core.rating.score_to_rating(score)
        "details": {...},                # anything you want to keep
    }
```

## How signals are integrated

Everyone writes their module in their own style; they're integrated **without
editing their files** via wrappers in `core/adapters.py`. Each adapter runs the
author's code and returns it on a common **1–10 scale** (`ten`) plus the
author's own `native_rating` and a `breakdown`. Authors already on 1–10 pass
through; others are converted from their native range (anshu −15..21, diya 0–1).
Insider labels (cosmo's BULLISH/BEARISH/NEUTRAL) map to fixed 1–10 anchors so
they count in comparisons and the composite. The displayed rating for every
signal — and the composite — comes from one shared vocabulary derived from the
1–10 score (each author's own wording is kept in the breakdown). The
**composite** is the equal-weighted average of the numeric 1–10 signals, and the
recommender ranks by it. *(Per-factor weighting is planned — see the
methodology doc.)*

A module that follows `analyze(ticker, period) -> {"score" in [-1,1], ...}`
natively needs no adapter; the runner converts its score onto 1–10.

Full details of each factor's calculation are in
**[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)**.

## Layout

```
app.py              CLI entry point
dashboard.py        Tkinter desktop app (branded; Single Ticker + Recommender)
modules/<name>.py   one signal per person
modules/stock_info.py  basic info (name, sector, industry, OHLC) + name search
core/
  registry.py       discovers modules + their metadata
  runner.py         runs all signals for a ticker; builds the 1-10 composite
  scoring.py        1-10 conversions + labels
  adapters.py       wrappers that put each author's signal on 1-10
  recommender.py    ranks a basket long -> short by the 1-10 composite
  universe.py       sector + mega-cap equity baskets to rank from (no indices)
  massive.py        Massive/Polygon REST client
  env.py            auto-loads .keys.env
docs/METHODOLOGY.md per-factor + composite calculation reference
```

The tool analyses **individual equities**. Market indices (DOW30, NASDAQ100,
S&P 500) are not selectable inputs — the recommender's presets are baskets of
individual stocks (sectors, mega-caps).
