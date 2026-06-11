# FlexTrade — Methodology

How every factor is calculated, how they're put on one scale, and how they're
combined into the composite index. This documents the system **as it works
today**. Weighting (making some factors count more than others) is noted at the
end as future work — right now every scored factor is weighted equally.

---

## 1. The pipeline at a glance

```
ticker ──► run all signals concurrently ──► put each on a 1–10 scale ──► average ──► composite (1–10) ──► rating
```

1. A ticker is handed to `core/runner.py::analyze_ticker`.
2. Every registered signal in `modules/` runs **in parallel** (they're I/O-bound
   API calls), each producing a score in its own units.
3. Each score is converted onto a **common 1–10 scale** (`ten`).
4. The **composite** is the simple mean of the available 1–10 scores.
5. A single **canonical rating** vocabulary is applied to every 1–10 number
   (per-signal and composite) so everything reads the same way.

Results are cached per `(ticker, period)`, so re-analyzing is instant.

---

## 2. The common 1–10 scale

Different authors score in different units. To compare and average them, every
numeric signal is mapped onto **1–10** (`core/scoring.py`).

**Linear map** for a signal whose native value sits in a known range `[lo, hi]`:

```
ten = clamp( (value − lo) / (hi − lo) × 9 + 1 , 1 , 10 )      # rounded to 1 dp
```

So `value = lo → 1.0`, `value = hi → 10.0`, linear in between, clamped.

Three families of signals feed this map:

| Native form of the signal | How it reaches 1–10 |
|---|---|
| **Already 1–10** (macd, rsi, sma, ema, momentum, ratios) | passed through unchanged |
| **Symmetric score in [−1, +1]** (sma_crossover, news, short_volume) | `to_ten(value, −1, +1)` → −1 = 1.0, 0 = 5.5, +1 = 10.0 |
| **Author's own numeric range** (dividends, short_interest, liquidity) | `to_ten(value, lo, hi)` with that author's `[lo, hi]` |

The author-range mappings currently configured (`core/scoring.py::NATIVE_RANGES`):

| Signal | Native range `[lo, hi]` |
|---|---|
| `dividends` (anshu) | `[−15, 21]` |
| `short_interest` (anshu2) | `[−8, 12]` |
| `liquidity` (diya) | `[0, 1]` |

**Qualitative signal (insider):** has no number, so its label maps to a fixed
1–10 anchor (`INSIDER_SIGNAL_TEN`) so it can still participate in the average:

```
BULLISH → 8.0     NEUTRAL → 5.0     BEARISH → 2.0
```

If a signal can't produce a value (no data, API error, timeout), its `ten` is
`None` and it is **excluded** from the composite — it never silently counts as a
neutral vote.

---

## 3. The canonical rating vocabulary

Every 1–10 number — each signal **and** the composite — is labelled with the
**same five-word vocabulary** (`core/scoring.py::ten_to_label`):

| 1–10 score | Rating |
|---|---|
| ≥ 8.0 | **STRONG BUY** |
| ≥ 6.5 | **BUY** |
| ≥ 4.5 | **HOLD** |
| ≥ 3.0 | **SELL** |
| < 3.0 | **STRONG SELL** |

This is the only rating shown in the UI, so factors are directly comparable.
Each author's *own* wording (e.g. aarav's "Sell / Avoid", diya's "STRONG",
cosmo's "BEARISH") is preserved in the per-signal **breakdown** for reference,
but it does not drive the headline rating.

---

## 4. The factors

13 signals across three categories. Each lists **what it measures**, **how it
scores**, and **how it lands on 1–10**.

### Technicals

**`momentum` — cross-sectional momentum (samar)**
Computes 12-1, 6-1 and 3-1 month price momentum and standardizes each as a
z-score against a diverse reference universe. The combined z-score (with a
sector-relative tiebreaker) becomes a 1–10 score directly. High = strong
relative momentum.

**`macd` — MACD trend (aarav)**
MACD (12/26/9) evaluated across multiple timeframes; volume confirmation and a
200-day moving-average floor (a pullback inside an uptrend isn't scored as a
sell). Emits a 1–10 composite directly.

**`rsi` — RSI regime (aarav2)**
Relative Strength Index with its current level, regime (how long it's been
over/under-bought), and historical percentile. Emits 1–10.

**`sma` / `ema` — moving-average structure (aarav3 / aarav4)**
Price relative to a set of simple / exponential moving averages and their
slopes and crossovers. Each emits a 1–10 composite.

**`short_volume` — short-volume pressure (aarav6)**
Looks at the daily short-volume ratio over ~30 days. Produces a 1–10 score,
re-expressed as a symmetric `[−1, +1]` and mapped back onto 1–10 by the runner.

**`sma_crossover` — 20/50 SMA crossover (kavin)**
Compares the 20-day vs the 50-day SMA. Bullish crossover (20 above 50) →
positive; bearish → negative. Native score is in `[−1, +1]`, mapped to 1–10.

### Fundamentals

**`ratios` — financial-ratio scorecard (justin)**
Scores valuation/profitability/leverage ratios **relative to the company's
sector**, each sub-metric on 1–10, combined into a 1–10 score.

**`dividends` — dividend quality (anshu)**
Dividend history, growth and payout-ratio sustainability. Native score on a
`[−15, +21]` scale, mapped to 1–10.

**`liquidity` — cash-flow liquidity (diya)**
Operational (operating cash flow) and financial (free cash flow) sub-scores
combined into a `0…1` composite, mapped to 1–10. *(Requires Massive Advanced-tier
fundamentals; otherwise excluded with "no data".)*

### Sentiment

**`short_interest` — short interest & squeeze risk (anshu2)**
Short interest as a % of float, days-to-cover, and squeeze conditions. Native
score on `[−8, +12]`, mapped to 1–10. (High short interest is bearish, but an
extreme reading can flag squeeze potential.)

**`insider` — insider-transaction sentiment (cosmo)**
Recent Form-4 insider buys vs sells → a qualitative `BULLISH / NEUTRAL /
BEARISH`, anchored to `8 / 5 / 2` on the 1–10 scale.

**`news` — news sentiment (raghav)**
Pulls recent articles from the Massive news endpoint and reads each article's
per-ticker *insight* (sentiment + reasoning). Sentiment is averaged
(`positive = +1, neutral = 0, negative = −1`) into a `[−1, +1]` score, mapped
to 1–10. The dashboard also shows the underlying headlines grouped by sentiment,
with links. If there is no recent coverage, the factor is excluded rather than
scored neutral.

---

## 5. The composite index

```
composite = mean( ten for every signal that produced a usable score )   # 1 dp
```

- A plain, **equal-weighted average** of the available 1–10 signals.
- Signals that errored / timed out / had no data are simply **left out** of the
  average (they don't drag it toward the middle).
- The composite gets the **same canonical rating** (§3) as any single signal.
- `n_scored` records how many signals actually contributed.

Worked example (AAPL, illustrative): 13 signals score, e.g. `momentum 6.9,
macd 3.2, rsi 5.8, … , liquidity 8.6, sma_crossover 10.0, news 5.1`. Their mean
≈ `6.3` → **HOLD**.

---

## 6. The recommender (Long / Short)

For a basket of tickers (`core/recommender.py`):

1. Compute each ticker's composite.
2. Sort highest → lowest.
3. Label the top `long_frac` (default **30%**) **Long**, the bottom `short_frac`
   (default **30%**) **Short**, the middle **Neutral**.

The ranking is purely cross-sectional on the composite — best names to be long
at the top, best to short at the bottom.

> Note: the tool ranks **individual equities**. Market indices (e.g. DOW30,
> NASDAQ100, S&P 500) are intentionally *not* offered as inputs, since an index
> isn't a single tradable equity. The recommender's presets are convenience
> baskets of individual stocks (sectors, mega-caps).

---

## 7. Weighting (future work)

Today the composite is an **equal-weighted** mean — every scored factor counts
the same. That's a deliberate v1 simplification.

The intended next step is **per-factor weights** so that, e.g., a
high-conviction fundamental can count more than a noisy short-horizon technical,
and factors can be down-weighted when their inputs are stale or low-confidence.
That will live alongside `core/scoring.py` / `core/runner.py` as a weight map
applied in the composite step (`Σ wᵢ·tenᵢ / Σ wᵢ` instead of a plain mean). It is
**not implemented yet**; this document will be updated when it lands.

---

## 8. Data, sources & robustness

- **Data:** Polygon.io and Massive REST APIs (prices, fundamentals, short data,
  insider transactions, news). Keys via `POLYGON_API_KEY` / `MASSIVE_API_KEY`.
- **Concurrency:** all signals run in a thread pool; each has a per-signal
  timeout so one slow/broken module can't stall a run.
- **Isolation:** an import error or exception in one teammate's module is
  captured and shown as that signal's status — it never takes down the analysis.
- **Caching:** results are memoized per `(ticker, period)`.
```
