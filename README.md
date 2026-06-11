# FlexTrade — Multi-Factor Equity Research

Each person's signal (in `modules/`) is run across tickers, shown on a common
**1–10 scale** with the author's own rating + reasoning, combined into a 1–10
composite, and surfaced three ways: a terminal breakdown, a CSV export, and a
branded desktop dashboard with search-by-name and a long/short recommender.

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
they count in comparisons and the composite. The **composite** is the average of the numeric 1–10
signals — the same scale the individual signals use — and the recommender ranks
by it.

A module that follows `analyze(ticker, period) -> {"score" in [-1,1], ...}`
natively needs no adapter; the runner converts its score onto 1–10.

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
  recommender.py    ranks a universe long -> short by the 1-10 composite
  universe.py       indices + sector baskets to rank from
  massive.py        Massive/Polygon REST client
  env.py            auto-loads .keys.env
```

> Signals without real logic yet return a placeholder score, so the whole
> pipeline runs end-to-end. Replace your `analyze` body (or add an adapter) to go live.
