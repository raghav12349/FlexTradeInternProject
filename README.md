# FlexTrade Intern Project — Equity Research Bot

Seven signals (one per person, in `modules/`) are run across tickers, combined
into a composite, and surfaced three ways: a terminal breakdown, a CSV export,
and a desktop dashboard with a long/short recommender.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export POLYGON_API_KEY=your_key_here   # modules that fetch live data use this
```

## Run

```bash
python app.py AAPL                 # one ticker: per-signal breakdown + composite
python app.py AAPL NVDA TSLA       # several: comparison table -> data/ratings.csv
python app.py --universe TECH --rank   # rank long->short -> data/rankings.csv
python dashboard.py                # desktop app: Single Ticker + Recommender tabs
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

The score must be normalized to `[-1, 1]` (−1 = strong short, +1 = strong long)
so it's comparable across people and feeds the composite. Fetch data however
you like; `core.massive` / your own client / `requests` are all fine.

## Don't follow the contract yet?

If your module is a self-contained script with a different shape (e.g. prints,
returns a 1–10 score), it can still be integrated without changing your file:
add an entry to `core/adapters.py` that calls your functions and normalizes the
result. `aarav` is wired this way as the reference example. Conforming to the
contract above is preferred long-term; adapters are the bridge until then.

## Layout

```
app.py              CLI entry point
dashboard.py        Tkinter desktop app (Single Ticker + Recommender tabs)
modules/<name>.py   one signal per person
core/
  registry.py       discovers modules + their metadata
  runner.py         runs all signals for a ticker; builds the composite
  recommender.py    ranks a universe long -> short
  universe.py       resolves tickers / named baskets
  rating.py         score -> Strong Sell..Strong Buy
  adapters.py       wrappers for non-conforming modules
  massive.py        optional Massive REST client
```

> Signals without real logic yet return an arbitrary placeholder score, so the
> whole pipeline runs end-to-end today. Replace your `analyze` body to go live.
