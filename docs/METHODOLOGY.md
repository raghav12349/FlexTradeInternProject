# FlexTrade — Methodology

Exactly how every factor's value is derived, how the factors are put on one
scale, and how they're combined into the composite. This documents the system
**as it works today**. Per-factor weighting is noted at the end as future work —
right now the composite is an equal-weighted mean.

---

## 1. Pipeline

```
ticker ─► run all signals concurrently ─► put each on a 1–10 scale ─► average ─► composite (1–10)
```

1. `core/runner.py::analyze_ticker(ticker, period)` is the entry point.
2. Every registered signal in `modules/` runs **in parallel** (I/O-bound API
   calls), each producing a number in its own units.
3. Each is converted to a common **1–10 scale** (`ten`) — see §2.
4. The **composite** is the mean of the available 1–10 values (§5).
5. Each signal shows **its author's own rating wording**; the composite shows
   the single house rating vocabulary (§3).

Results are cached per `(ticker, period)`.

---

## 2. Putting every signal on 1–10

The core conversion (`core/scoring.py`):

```
to_ten(value, lo, hi) = clamp( (value − lo) / (hi − lo) × 9 + 1 , 1 , 10 )   # 1 dp
```

`value = lo → 1.0`, `value = hi → 10.0`, linear between, clamped. Three families:

| Native form | How it becomes 1–10 |
|---|---|
| **Already 1–10** — macd, rsi, sma, ema, momentum, ratios, **dividends** | passed through unchanged |
| **Symmetric `[−1, +1]`** — sma_crossover, news, short_volume | `to_ten(value, −1, +1)` → −1 = 1.0, 0 = 5.5, +1 = 10.0 |
| **Author's own signed range** — short_interest, liquidity | `to_ten(value, lo, hi)` with that author's range |

Author ranges currently used by the adapters:

| Signal | Range `[lo, hi]` |
|---|---|
| `short_interest` (anshu2) | `[−8, 12]` |
| `liquidity` (diya) | `[0, 1]` |

**Qualitative signal — `insider` (cosmo):** no number, so its label maps to a
fixed 1–10 anchor: `BULLISH → 8.0`, `NEUTRAL → 5.0`, `BEARISH → 2.0`.

If a signal can't produce a value (no data / API error / timeout), its `ten` is
`None` and it is **excluded** from the composite — never counted as a neutral
vote.

---

## 3. Ratings

- **Per signal:** the dashboard and terminal show **the author's own wording**
  (e.g. aarav's "Sell / Avoid", anshu's "BUY", diya's "STRONG", cosmo's
  "NEUTRAL") next to that signal's 1–10 score.
- **Composite only:** labelled with one **house vocabulary** derived from the
  1–10 score (`core/scoring.py::ten_to_label`):

| Composite 1–10 | House rating |
|---|---|
| ≥ 8.0 | STRONG BUY |
| ≥ 6.5 | BUY |
| ≥ 4.5 | HOLD |
| ≥ 3.0 | SELL |
| < 3.0 | STRONG SELL |

---

## 4. The factors — explicit derivations

13 signals in three categories. Each entry gives the **inputs**, the **exact
scoring logic**, and the **range it lands on**.

### Technicals

**`momentum` — cross-sectional momentum (samar)**
- Inputs: trailing returns → **12-1**, **6-1**, **3-1** month momentum
  (i.e. 12-month return skipping the most recent month, etc.).
- Each horizon is standardized to a robust **z-score** (median/MAD) across the
  **S&P 500** constituents, and `z_combined = min(z₃, z₆, z₁₂)` (a name only gets
  credit if *all three* windows are strong).
- The **1–10 score is the percentile rank of `z_combined` within the S&P 500**
  (`1 + pct·9`) — so it's momentum *relative to the index*. This matches samar's
  own `run_single` vs S&P 500 (its menu option 1). The "shape" (accelerating /
  fading) is a secondary tiebreaker. Reference index is configurable via
  `_SAMAR_INDEX_KEY` in `core/adapters.py`.

**`macd` — MACD trend (aarav)**
- MACD(12/26/9) computed, then scored per timeframe starting from a base of
  **5.0** with these additive components:
  - **±2.0** MACD line vs signal line (weight 30%)
  - **±0.5 … ±2.5** sign & size of the **histogram regression slope** over the
    window, normalized by the full histogram range (weight 35%)
  - **±0.75** current histogram sign (15%)
  - **±0.5** MACD above/below zero (10%)
  - **±1.0** a fresh zero-line cross inside the window (10%)
  - volume-confidence nudge (expanding volume confirms; contracting dampens)
- A **200-day MA floor** keeps a pullback inside an uptrend from scoring as a
  sell. Scores across timeframes are combined into a **1–10** composite.

**`rsi` — RSI regime (aarav2)**
- Wilder RSI; the current RSI's **historical percentile** drives the base score:
  percentile ≤10 → 10.0, ≤20 → 9.0, ≤30 → 8.0, … ≤80 → 3.0, ≤90 → 2.0, else 1.0
  (low RSI = oversold = bullish = high score).
- Adjusted by regime (how long it's been over/under-bought) and context →
  **1–10**.

**`sma` / `ema` — moving-average structure (aarav3 / aarav4)**
- Price relative to a set of simple / exponential moving averages, plus their
  slopes and crossovers, with 200-day MA context → a **1–10** composite each.

**`short_volume` — short-volume pressure (aarav6)**
- From ~30 days of FINRA short-volume data, a weighted 1–10 score:
  - **40%** Short-Volume Ratio (latest `short_vol / total_vol`): ≥0.65 → 1.5,
    ≥0.55 → 3.0, ≥0.48 → 4.5, ≥0.42 → 5.5, ≥0.35 → 7.0, else 9.0
  - **30%** SVR **trend** (last-5-day avg − prior-5-day avg): sharply rising →
    1.5 … sharply falling (covering) → 9.0
  - **15%** exempt ratio (`short_exempt / short_vol`; high = market-maker hedging,
    less bearish)
  - **15%** volume surge (`avg_5d / avg_20d` short volume)
- Weighted sum clamped to **1–10**. (High short pressure → low score.) The module
  re-expresses it as `[−1, +1]` and the runner maps it back to 1–10.

**`sma_crossover` — 20/50 SMA crossover (kavin)**
- `spread = (SMA20 − SMA50) / SMA50`.
- `score = clamp(spread / 0.05, −1, +1)` — i.e. the spread **saturates at ±5%**.
- Positive = 20-day above 50-day (bullish), negative = below. Mapped to **1–10**.

### Fundamentals

**`ratios` — sector-relative ratio scorecard (justin)**
- Picks a **sector profile**; each profile lists metrics with a `good` anchor, a
  `bad` anchor, a direction, and a weight. E.g. Technology: P/E (low, good 15 /
  bad 45, w 0.15), revenue growth (high, good 25 / bad 0, w 0.25), ROE (high,
  good 20 / bad 6, w 0.20), D/E (low, good 0.3 / bad 2.0, w 0.25), EV/Sales
  (low, good 2 / bad 15, w 0.15).
- Each metric is linearly scored **bad → 1, good → 10** (direction-aware;
  negatives where they shouldn't be → 1), then a **weighted average** over the
  available metrics gives **1–10** (no data → 5.0 neutral).

**`dividends` — dividend quality (anshu)**
- Starts at **5.0**, then adds/subtracts:
  - **payout ratio:** very low → +2.0 … dangerously high → −2.5
  - **growth streak:** consecutive years of dividend growth → positive; cuts →
    negative
  - **yield / growth-rate** adjustments
- Clamped to **1–10** and passed straight through (the author's own thresholds
  give the wording: ≥8 STRONG BUY, ≥6.5 BUY, ≥5.5 MILD BUY, ≥4.5 NEUTRAL, ≥3
  CAUTION, else AVOID). Non-dividend payers are excluded.

**`liquidity` — cash-flow liquidity (diya)**
- An **operational** sub-score (operating cash flow) and a **financial**
  sub-score (free cash flow), combined `0.4 × operational + 0.6 × financial`
  into a `0…1` composite over annual periods, mapped to **1–10**.
- Requires Massive Advanced-tier fundamentals; otherwise excluded ("no data").

### Sentiment

**`short_interest` — short interest & squeeze (anshu2)**
- A **signed** score (~`[−8, +12]`) built from short interest as a % of float,
  days-to-cover, and squeeze conditions (high short interest is bearish, but an
  extreme reading flags squeeze potential, which can flip the signal). Mapped via
  `to_ten(−8, 12)`.

**`insider` — insider-transaction sentiment (cosmo)**
- Open-market **buys** (Form-4 code `P`) score bullish, **sells** (`S`) bearish.
  Each transaction is weighted by `shares × role_weight + log10(value)·…`, where
  `role_weight` ranks officers/directors/10%-owners; **sells are discounted
  ×0.25**; **cluster buying** (≥3 distinct insider buyers) multiplies the bull
  score ×1.3.
- `net_ratio = (bull − bear) / (bull + bear)`. `net_ratio > threshold` →
  **BULLISH**. **BEARISH** requires several gates (no offsetting buys, a minimum
  number of distinct sellers, and sell value above a minimum fraction of market
  cap) — otherwise **NEUTRAL** (insider selling alone is treated as weak signal).
- Label → 1–10 anchor: BULLISH 8 / NEUTRAL 5 / BEARISH 2.

**`news` — news sentiment (raghav)**
- Pulls recent articles from the Massive news endpoint and reads each article's
  per-ticker **insight** (`sentiment` + `sentiment_reasoning`).
- Sentiment is averaged: `positive = +1, neutral = 0, negative = −1`, giving a
  `[−1, +1]` mean → `to_ten(−1, +1)`. No recent coverage → excluded.
- The dashboard also groups the underlying headlines by sentiment, with links.

---

## 5. The composite index — weighted

The composite is a **weighted average** of the available 1–10 signals
(`core/weights.py`, `core/runner.py`):

```
composite = Σ (tenᵢ × weightᵢ) / Σ weightᵢ        # over signals that scored, 1 dp
```

- Each factor contributes in proportion to its weight (table below).
- Errored / timed-out / no-data signals are **dropped** and the denominator
  shrinks (weights renormalise over what's available), so a missing signal never
  distorts the result. `n_scored` reports how many contributed.
- The composite gets the house rating from §3.

**Weights** (from the *Ultra Composite Index* scheme in `COMPOSITE_INDEX.md`):

| Signal | Weight | | Signal | Weight |
|---|---|---|---|---|
| `sma` | 12% | | `macd` | 8% |
| `ratios` | 12% | | `news` | 8% |
| `ema` | 10% | | `short_volume` | 8% |
| `rsi` | 9% | | `insider` | 7% |
| `momentum` | 9% | | `liquidity` | 7% |
| `dividends` | 5% | | `sma_crossover` | 5% |

Any registered signal not in the table (e.g. `short_interest` / anshu2) uses a
default weight of **5%**. Rationale for each weight is documented in
`COMPOSITE_INDEX.md`.

Worked example (AAPL, illustrative): with `sma 6.4 (12%)`, `ratios 6.0 (12%)`,
`liquidity 8.6 (7%)`, `macd 3.2 (8%)`, `sma_crossover 10.0 (5%)`, … the
weighted average ≈ **6.2** → **HOLD** (vs. ≈ 6.4 if equal-weighted — the lower
weights on the strongest outliers pull it down slightly).

---

## 6. The recommender (Long / Short)

For a basket (`core/recommender.py`): compute each ticker's composite, sort
high→low, label the top `long_frac` (default **30%**) **Long**, the bottom
`short_frac` (default **30%**) **Short**, the middle **Neutral**.

Baskets are resolved **live from the web** (`core/universe.py`): an index name
(`SP500`) or a sector name (`tech`, `healthcare`, `energy`, …) is expanded to its
current constituents from the S&P 500 dataset (which carries each company's GICS
sector); anything else is parsed as a list of tickers. Falls back to small static
baskets if offline.

---

## 7. Weighting

Implemented (§5): the composite is a **weighted** average using the weights in
`core/weights.py`, renormalised over whichever signals scored. To change the
scheme, edit `COMPOSITE_WEIGHTS` (or `DEFAULT_WEIGHT` for unlisted signals) —
the runner, recommender, dashboard and terminal all read from it. The full
rationale for each weight (with academic references) is in `COMPOSITE_INDEX.md`.

Not yet implemented from that scheme: the **multi-gate filter** (eight
pass/fail conditions that can downgrade a Buy to Hold). Only the weighting is
wired in so far.

---

## 8. Data, sources & robustness

- **Data:** Polygon.io and Massive REST APIs (prices, fundamentals, short data,
  insider transactions, news). Keys via `POLYGON_API_KEY` / `MASSIVE_API_KEY`.
- **Web universe:** S&P 500 constituents + GICS sectors fetched live and cached.
- **Concurrency:** all signals run in a thread pool with a per-signal timeout.
- **Isolation:** an import error or exception in one teammate's module is
  captured and shown as that signal's status — it never takes down the run. (The
  registry also seeds a builtins key shim so a module that references an
  undefined `MASSIVE_API_KEY` at import time still loads.)
- **Caching:** results are memoized per `(ticker, period)`.
