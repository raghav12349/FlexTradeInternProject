# FlexTrade Ultra Composite Index — Methodology & Weightage Documentation

**Module:** `modules/aarav_ultra.py`  
**Author:** Aarav Gupta  
**Version:** 1.0 — June 2026  

---

## Overview

The Ultra Composite Index aggregates **12 independent signals** across four analytical categories — Technical, Fundamental, Quantitative, and Alternative Data — into a single score on a **1–10 scale**. Each signal is normalised to the same scale before weighting, so no signal dominates by virtue of its native unit. Signals that fail to fetch data are dropped and the remaining weights are **renormalised to sum to 1.0**, ensuring the composite is always fully utilised.

A **multi-gate filter** then validates every Buy or Strong Buy signal against eight independent conditions before the label is confirmed. This prevents the composite score from recommending a purchase in adverse conditions even when the weighted average looks strong.

---

## Output Scale

| Score | Recommendation |
|-------|---------------|
| ≥ 8.5 | **Strong Buy** |
| ≥ 6.5 | **Buy** |
| ≥ 4.5 | **Neutral / Hold** |
| ≥ 2.5 | **Sell / Avoid** |
| < 2.5 | **Strong Sell** |

---

## Signal Architecture

### Category 1 — Technical Indicators (39% combined)

These signals are derived directly from price and volume history. They are the highest-frequency inputs and are the most directly tradeable for swing and position trading strategies. Technical signals are the dominant input category because they react first to changing market conditions and are independent of reporting lags that affect fundamental data.

---

#### 1. SMA — Simple Moving Average
**Weight: 12% — highest individual weight**  
**Source:** `aarav3.py`

The SMA signal evaluates trend structure using three moving averages (20-day, 50-day, 200-day) across four equally-weighted components:

| Component | Sub-weight | What it measures |
|-----------|-----------|-----------------|
| Stack alignment | 35% | Price > SMA20 > SMA50 > SMA200 (full bull stack) |
| Crossover | 25% | Golden cross (SMA50 crosses above SMA200) vs death cross |
| Slope | 20% | Least-squares slope of SMA50 — is the trend accelerating? |
| Extension | 20% | How far price has stretched from SMA20 (reversion risk) |

A volume-confidence nudge is applied: high institutional volume during an uptrend adds up to 0.5 points; low volume subtracts up to 0.5 points.

**Why 12%:** SMA is the most reliable technical baseline for identifying whether a stock is structurally in an uptrend or downtrend. Unlike faster indicators, it does not whipsaw on noise. Academic research (Brock, Lakonishok, LeBaron, 1992 — *Journal of Finance*) demonstrated that moving average rules generated significant excess returns in US equity markets over 90 years. SMA receives the highest individual weight because it is the structural foundation everything else should confirm.

---

#### 2. EMA — Exponential Moving Average
**Weight: 10%**  
**Source:** `aarav4.py`

Identical four-component framework to SMA (stack alignment, crossover, slope, extension) but uses exponential weighting, which gives more weight to recent prices. The same windows (20, 50, 200 days) are used.

**Why 10% vs SMA's 12%:** EMA reacts faster to price changes than SMA, which makes it a better early-warning signal but also more susceptible to whipsaw. It acts as a confirmation and early-signal layer on top of SMA rather than replacing it. The 2% discount relative to SMA reflects the higher false-positive rate in choppy or sideways markets.

---

#### 3. RSI — Relative Strength Index
**Weight: 9%**  
**Source:** `aarav2.py`

A trader-grade RSI implementation with five weighted components:

| Component | Sub-weight | What it measures |
|-----------|-----------|-----------------|
| Percentile rank | 30% | Where current RSI sits vs its own 1-year history |
| Divergence | 25% | Price making new highs while RSI does not (bearish), or vice versa |
| Regime | 20% | Bull regime (RSI holds above 40) vs bear regime (RSI fails below 60) |
| Momentum | 15% | Rate of change of RSI itself |
| Failure swings | 10% | Classic RSI failure swing patterns (reversal signals) |

**Why 9%:** RSI is the premier momentum-timing indicator. It excels at identifying when a trend is overextended and due for a reversal — an area where SMA and EMA are blind. Wilder (1978) originally proposed RSI as a timing tool, and subsequent research has confirmed that combining RSI with trend filters significantly improves trade timing. RSI also provides the single most important gate condition in the multi-gate filter (RSI < 5 suppresses any Buy).

---

#### 4. MACD — Moving Average Convergence Divergence
**Weight: 8%**  
**Source:** `aarav.py`

Multi-timeframe MACD scoring computed across the available price history. The signal evaluates MACD line crossovers, histogram direction, signal line gaps, and zero-line positioning across multiple lookback windows. Results are averaged across timeframes to reduce sensitivity to any single period.

A 200-day moving average floor was removed in a previous iteration — the signal now reflects actual bearish conditions rather than artificially flooring to neutral when price is above the 200-day MA.

**Why 8%:** MACD captures momentum *acceleration* — not just direction. A rising MACD in an uptrend confirms the trend is strengthening; a falling MACD warns of deceleration before the trend reverses. It sits below RSI because MACD has a higher sensitivity to parameter choice (fast/slow periods) and generates more false signals in flat markets. At 8%, it adds meaningful acceleration context without allowing it to override the more stable structural signals.

---

#### 5. SMA Crossover (Confirmation)
**Weight: 5%**  
**Source:** `kavin.py` — 20-day vs 50-day SMA spread

Measures the percentage spread between the 20-day and 50-day SMA. Score saturates at ±1 (mapped to 1–10) when the spread reaches ±5%. A positive score means the fast MA leads the slow MA — bullish. A negative score means the slow MA has overtaken the fast — bearish.

**Why 5% (confirmation tier):** This signal overlaps significantly with the SMA stack alignment component in `aarav3.py`. It is included as a lightweight confirmation layer from an independent calculation (different author, different codebase path). The overlap is intentional — when two independent SMA implementations agree, conviction increases. At 5%, it adds signal reinforcement without double-counting at full weight.

---

### Category 2 — Fundamental Indicators (24% combined)

Fundamental signals have a longer lag (financial statements are quarterly) but anchor the analysis in intrinsic value. Academic research has consistently shown that quality and value factors deliver persistent alpha over multi-year horizons even when technical signals are neutral.

---

#### 6. Financial Ratios — Fundamental Quality
**Weight: 12%**  
**Source:** `financial_ratios_rater.py`

Sector-appropriate financial ratio scoring. The SIC code of the company determines which ratio set is applied — technology companies are scored differently from financials or utilities. Ratios evaluated include (sector-dependent):

- Price-to-Earnings (P/E)
- Return on Equity (ROE)
- Debt-to-Equity
- Gross and operating margins
- Revenue growth rate
- Free cash flow yield

Each ratio is scored on a 1–10 scale against sector benchmarks and weighted within the ratio composite.

**Why 12% — tied with SMA for highest weight:** The Fama-French three-factor model (1992, 1993) established that a quality/value factor explains persistent return differences across stocks beyond what market beta alone predicts. A stock with strong fundamentals has a higher intrinsic value floor, meaning pullbacks are buying opportunities rather than deterioration signals. A stock with weak fundamentals (score < 4.0) triggers the F1 gate — no Buy signal is issued regardless of technical strength. This gate has saved false positives in cases where a technically strong stock is in secular fundamental decline.

---

#### 7. Dividends / Income Signal
**Weight: 5%**  
**Source:** `anshu.py`

Evaluates dividend history over a 2-year window: payout consistency, growth rate, payout ratio health, and market-cap-adjusted sustainability. Non-dividend-paying stocks receive a neutral 5.0 score (growth stock assumption) rather than a penalty.

**Why 5%:** Dividends are a weak standalone predictor of near-term price performance — many studies (Ang & Bekaert, 2007) show dividend yield has modest predictive power at short horizons. However, consistent dividend payment signals cash flow health and management confidence in the business outlook. At 5%, it adds a mild value-factor tilt without distorting the composite for growth stocks that legitimately reinvest all cash flows.

---

### Category 3 — Quantitative / Statistical (17% combined)

---

#### 8. Price Momentum (Cross-Sectional)
**Weight: 9%**  
**Source:** Inline calculation from price history

Implements the canonical Jegadeesh-Titman (1993) cross-sectional momentum factor using two windows:

- **12-1 month momentum:** (price 21 trading days ago) / (price 252 trading days ago) − 1  
- **6-1 month momentum:** (price 21 trading days ago) / (price 126 trading days ago) − 1

The "−1 month" exclusion (using price from 21 days ago rather than today) is standard practice to avoid the short-term reversal effect documented in the original paper. Both windows are averaged and mapped to a score: −40% return maps to 1.0, 0% maps to 5.5, +40% maps to 10.0.

**Why 9%:** Jegadeesh and Titman's 1993 paper (*Journal of Finance*) is one of the most replicated findings in empirical finance. Stocks that have outperformed over the past 6–12 months continue to outperform over the next 3–12 months. Fama himself (a skeptic of most technical signals) has acknowledged momentum as a genuine factor. The 9% weight places it alongside RSI — both are momentum signals, but operating on different timescales: RSI captures near-term exhaustion, while price momentum captures medium-term continuation.

---

#### 9. Short Interest
**Weight: 8%**  
**Source:** `aarav6.py`

Analyses the short volume ratio trend over a 30-day window. High short interest relative to the stock's recent history is bearish (shorts are betting against it, or the float is constrained). Declining short interest (covering) is bullish. The raw 1–10 score from `aarav6` is mapped from its native [-1, 1] contract to [1, 10].

**Why 8%:** Rapach, Ringgenberg, and Zhou (2015, *Journal of Financial Economics*) demonstrated that aggregate short interest is among the strongest predictors of cross-sectional stock returns. Importantly, short sellers are informed traders — they do significant due diligence before taking a position. Extreme short interest (score < 3.0) triggers the F2 gate, suppressing Buy signals because elevated short positioning often precedes price declines. At 8%, it carries meaningful weight without allowing a single period of elevated shorting to override strong fundamentals and technicals.

---

### Category 4 — Alternative Data (27% combined)

Alternative data signals carry information that is orthogonal to price and financial statements. Academic research increasingly validates these signals, particularly for short-horizon alpha generation.

---

#### 10. News Sentiment
**Weight: 8%**  
**Source:** `raghav_news.py` — Massive API news endpoint with per-article insights

Fetches recent news articles for the ticker and reads Massive's per-article `insights` field, which provides a sentiment label (positive / neutral / negative) and reasoning specific to that ticker extracted by an AI model. The score is the mean sentiment across recent articles, mapped from [-1, 1] to [1, 10].

**Why 8%:** Tetlock (2007, *Journal of Finance*) showed that negative media sentiment in the Wall Street Journal predicted downward pressure on market prices and higher trading volume the next day. Subsequent research (Tetlock, Saar-Tsechansky, Macskassy, 2008) found that the fraction of negative words in financial news predicts firms' earnings and stock returns. At 8%, news sentiment is a meaningful near-term modifier. Strongly negative news (score < 2.5) triggers the S1 gate, blocking Buy signals even when all other signals are positive — a recent adverse news event can rapidly invalidate a previously valid technical setup.

---

#### 11. Insider Activity (Form 4)
**Weight: 7%**  
**Source:** Polygon/Massive Form 4 API endpoint (90-day lookback)

Fetches Form 4 SEC filings (insider transactions) over the past 90 days. Each transaction is weighted by:
- **Transaction type:** Open-market purchases (code P) are fully counted; open-market sales (code S) are discounted by 75% because insiders sell for many reasons unrelated to outlook (diversification, estate planning, tax obligations)
- **Executive seniority:** CEO (1.5×), CFO (1.3×), Director (1.0×), etc.

The net weighted buy/sell ratio is mapped from [-1, 1] to [1, 10]. No filings in the lookback window → neutral score of 5.5.

**Why 7%:** Seyhun (1988, *Journal of Business*) documented that open-market insider purchases predict positive abnormal returns of ~3% over the following six months. Lakonishok and Lee (2001, *Review of Financial Studies*) confirmed that insider buying is more informative than insider selling — hence the 75% discount applied to sales. At 7%, insider activity carries weight comparable to short interest, reflecting that both are signals from informed market participants (corporate insiders and short sellers respectively) who trade on non-public assessments of firm value.

---

#### 12. Liquidity (Fundamental + Market)
**Weight: 7%**  
**Source:** `diya.py` (FCF/OCF ratios) with volume-proxy fallback

Primary source evaluates fundamental liquidity using cash flow statement metrics:
- **Operating Cash Flow ratio:** OCF / current liabilities — ability to meet near-term obligations from operations
- **Free Cash Flow ratio:** FCF / current liabilities — headroom after maintenance capital expenditure

Both ratios are normalised against anchor thresholds and combined (40% OCF, 60% FCF) into a band: STRONG (8.5) / ADEQUATE (6.5) / WATCH (4.0) / WEAK (2.0).

If the financial API is unavailable, a volume-proxy fallback is used: the 20-day average daily volume is compared to the 90-day average. A ratio ≥ 1.15 = STRONG (8.5), 0.85–1.15 = ADEQUATE (6.5), < 0.85 = LOW (3.5).

**Why 7%:** Liquidity is a risk factor, not an alpha factor — it does not predict return direction but it determines conviction size. An illiquid stock that is technically strong may not be executable at scale; a company with weak cash coverage faces amplified downside risk in adverse conditions. The weight was raised from an initial 3% after analysis showed that liquidity-distressed companies showed disproportionate false positives in the technical signals — the technical score looks strong because price is thin and easily moved, not because genuine institutional buyers are present.

---

## Composite Calculation

```
composite = Σ (signal_score_i × weight_i)  /  Σ weight_i  [for available signals only]
```

When a signal is unavailable (API failure, insufficient data), its weight is dropped and the denominator shrinks accordingly. The composite always reflects the full information available, and is never distorted by a missing signal returning an artificial neutral 5.5.

---

## Multi-Gate Filter

Even when the composite score falls in the Buy or Strong Buy range (≥ 6.5), **all eight gates must pass** for the label to be confirmed. A single gate failure downgrades the recommendation to **Neutral / Hold**.

| Gate | Condition | Signal checked | Reasoning |
|------|-----------|---------------|-----------|
| T1 | RSI ≥ 5.0 | `rsi` | Score below 5 = momentum exhausted or oversold in a downtrend |
| T2 | SMA ≥ 6.5 | `sma` | Structural stack must confirm — price/MA alignment is the bedrock |
| T3 | SPY above 50d SMA | market | Bull market regime — individual stock longs have much lower base rate in broad market downtrend |
| T4 | Volume not trending low | volumes | 20d avg < 85% of 90d avg = drying institutional participation |
| T5 | No recency surge | prices | Price up >12% in 20 days = extended, mean-reversion risk elevated |
| F1 | Ratios ≥ 4.0 | `ratios` | Fundamental wreck — technical strength may be dead-cat bounce |
| F2 | Short interest ≥ 3.0 | `short` | Extreme short pressure — informed bearish positioning |
| S1 | News ≥ 2.5 | `news` | Strongly negative sentiment — adverse news can rapidly invalidate technical setups |

Gates only apply to Buy and Strong Buy. Sell / Avoid and Strong Sell pass through the gate layer unchanged, as do Neutral / Hold signals.

---

## Weight Summary

| Rank | Signal | Category | Weight |
|------|--------|----------|--------|
| 1 | SMA (aarav3) | Technical | **12%** |
| 1 | Financial Ratios | Fundamental | **12%** |
| 3 | EMA (aarav4) | Technical | **10%** |
| 4 | RSI (aarav2) | Technical | **9%** |
| 4 | Price Momentum | Quantitative | **9%** |
| 6 | MACD (aarav) | Technical | **8%** |
| 6 | News Sentiment | Alternative | **8%** |
| 6 | Short Interest | Quantitative | **8%** |
| 9 | Insider Activity | Alternative | **7%** |
| 9 | Liquidity | Alternative | **7%** |
| 11 | Dividends | Fundamental | **5%** |
| 11 | SMA Crossover | Technical | **5%** |
| | **Total** | | **100%** |

---

## Key Academic References

| Paper | Finding | Applied in |
|-------|---------|-----------|
| Brock, Lakonishok, LeBaron (1992) *J. Finance* | MA rules generate excess returns over 90-year US equity history | SMA, EMA weights |
| Jegadeesh & Titman (1993) *J. Finance* | Past 6–12 month winners continue to outperform 3–12 months forward | Momentum (9%) |
| Fama & French (1992, 1993) *J. Finance, J. Fin. Economics* | Quality/value factor explains persistent return differentials | Ratios (12%) |
| Seyhun (1988) *J. Business* | Open-market insider buys predict +3% abnormal return over 6 months | Insider (7%) |
| Lakonishok & Lee (2001) *Rev. Fin. Studies* | Insider buying more informative than selling; sales should be discounted | Insider sell discount 75% |
| Tetlock (2007) *J. Finance* | Negative WSJ sentiment predicts next-day market pressure and volume | News gate (S1) |
| Tetlock, Saar-Tsechansky, Macskassy (2008) | Negative words in news predict earnings and stock returns | News (8%) |
| Rapach, Ringgenberg, Zhou (2015) *J. Fin. Economics* | Aggregate short interest is among strongest cross-sectional return predictors | Short (8%), F2 gate |
| Wilder (1978) *New Concepts in Technical Trading Systems* | RSI as a timing tool for overbought/oversold conditions | RSI (9%), T1 gate |
| Ang & Bekaert (2007) *Rev. Fin. Studies* | Dividend yield has modest short-horizon predictive power | Dividends (5%) |

---

## Limitations

1. **Reporting lag:** Financial ratio data (ratios, dividends, liquidity) reflects the most recent quarterly filing. Deterioration between filings will not be captured until the next report.

2. **Renormalisation risk:** If several signals are simultaneously unavailable, the composite is computed from a smaller signal set. The renormalised weights may then over-concentrate in whatever signals are available.

3. **Correlation between signals:** SMA, EMA, SMA Crossover, and Price Momentum all respond to the same underlying price series. In trending markets they will all agree and amplify each other, which can produce very high composite scores that overstate conviction.

4. **Gate asymmetry:** The multi-gate filter only blocks Buy upgrades — it does not upgrade Sell signals even if most gates would pass. This is intentional (conservative design) but means the composite may understate buying opportunities during brief technical pullbacks in otherwise strong stocks.

5. **Insider data sparsity:** Many stocks have no Form 4 filings in any given 90-day window. These default to a neutral 5.5, which provides no signal. The insider weight of 7% is effectively wasted for these stocks and redistributed silently.
