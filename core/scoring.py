"""Shared 1-10 scoring scale.

Every numeric signal is shown on a common 1-10 scale so they're comparable and
can be averaged into a composite. Some authors already score on 1-10 (aarav,
samar, justin); others use their own range (anshu -15..21, diya 0-1) and we
convert. Qualitative insider labels (cosmo's BULLISH/BEARISH/NEUTRAL) map to
fixed points on the same scale so they participate in comparisons and composite.
"""
from __future__ import annotations

import math

# Native numeric range per signal, used to map onto 1-10. Signals already on
# 1-10 (aarav family, samar, justin, anshu/dividends) don't need an entry — they
# pass through. This documents the ranges the adapters use for signals that
# score on their own scale.
NATIVE_RANGES: dict[str, tuple[float, float]] = {
    "short_interest": (-8.0, 12.0),   # anshu2 (signed score)
    "liquidity": (0.0, 1.0),          # diya (0..1 composite)
}

# cosmo insider sentiment → comparable 1-10 anchor (included in composite).
INSIDER_SIGNAL_TEN: dict[str, float] = {
    "BULLISH": 8.0,
    "BEARISH": 2.0,
    "NEUTRAL": 5.0,
}

# Plain-English "how this rating was computed" line, shown at the top of every
# signal's breakdown in the UI/CLI so a reader understands the method before the
# numbers. Keyed by the registry signal name (see core/adapters.py + modules).
# Summaries condensed from COMPOSITE_INDEX.md.
SIGNAL_DESCRIPTIONS: dict[str, str] = {
    "sma": ("Trend structure from 20/50/200-day simple moving averages — stack "
            "alignment, golden/death cross, SMA50 slope and price extension, "
            "nudged by volume confidence."),
    "ema": ("Same trend framework as SMA but exponentially weighted to react "
            "faster to recent price (stack, crossover, slope, extension)."),
    "rsi": ("Momentum timing from RSI — its percentile vs the past year, "
            "price/RSI divergence, bull/bear regime, RSI momentum and failure "
            "swings."),
    "macd": ("Momentum acceleration from MACD across multiple timeframes — line "
             "crossovers, histogram direction, signal-line gap and zero-line "
             "position, averaged."),
    "sma_crossover": ("Percentage spread between the 20-day and 50-day SMA; fast "
                      "above slow is bullish, saturating at ±5%."),
    "ratios": ("Sector-appropriate financial ratios (P/E, ROE, debt/equity, "
               "margins, growth, FCF yield) scored against sector benchmarks."),
    "dividends": ("Dividend quality over ~2 years — payout consistency, growth, "
                  "payout-ratio health and sustainability (non-payers = neutral)."),
    "momentum": ("Cross-sectional price momentum (12-1 and 6-1 month), excluding "
                 "the most recent month to avoid short-term reversal, vs peers."),
    "short_interest": ("Short-interest positioning — elevated or rising shorting "
                       "is bearish, covering is bullish (from its signed score)."),
    "short_volume": ("Short-volume ratio trend over ~30 days — high short volume "
                     "vs recent history is bearish, declining is bullish."),
    "news": ("Mean of per-article sentiment (positive/neutral/negative) read from "
             "each recent article's ticker-specific insight."),
    "insider": ("Form 4 insider trades over 90 days, weighted by type (buys full, "
                "sells discounted 75%) and executive seniority."),
    "liquidity": ("Cash-flow liquidity (operating & free cash flow vs current "
                  "liabilities), with a volume-based fallback when financials are "
                  "unavailable."),
}


# User-facing display label per signal. Internal keys stay stable (weights,
# caching, CSV columns, composite math all key off the original name); this only
# changes what the human reads. Unlisted signals show their plain name.
DISPLAY_NAMES: dict[str, str] = {
    "liquidity": "Cash Position",
}


def display_name(name: str) -> str:
    """User-facing label for a signal; internal key is unchanged."""
    return DISPLAY_NAMES.get(name, name)


# Plain-English, 2-3 line explanations for retail investors, shown in the little
# "ⓘ" info popup next to each signal. Deliberately jargon-light: what it looks at
# and what a higher score means. Keyed by the internal signal name.
SIGNAL_PLAIN: dict[str, str] = {
    "momentum": ("Blends three checks, each compared to other companies in the "
                 "same sector: (1) has the stock been climbing faster than its "
                 "peers over recent months, (2) is it still fairly priced for the "
                 "profits it earns (not too expensive vs earnings), and (3) is it "
                 "fairly priced versus what the company actually owns (its net "
                 "assets). Higher = real momentum in a stock you're not "
                 "overpaying for."),
    "macd": ("A trend gauge that compares the stock's short-term and longer-term "
             "average prices to see if upward momentum is building or fading. "
             "Higher = momentum is turning up."),
    "rsi": ("Tells you if a stock has run up too fast (overbought) or been sold "
            "off too hard (oversold) recently — a timing tool, since oversold "
            "stocks can bounce. Higher = more favourably positioned to rise."),
    "sma": ("Checks the price against its 20-, 50- and 200-day average prices. "
            "When price sits above its long-term averages and they line up "
            "upward, the trend is healthy. Higher = stronger uptrend."),
    "ema": ("Same trend check as the moving averages, but it weights recent "
            "prices more so it reacts faster. Higher = price is holding above "
            "its trend with momentum behind it."),
    "short_volume": ("Looks at how much recent trading was short-selling (bets "
                     "the stock falls). Rising short activity is bearish, fading "
                     "is bullish. Higher = less downward pressure from shorts."),
    "ratios": ("A company-health check using fundamentals — profitability, debt, "
               "margins, cash flow and how cheap or expensive the stock is — "
               "compared to what's normal for its sector. Higher = financially "
               "stronger and more reasonably priced."),
    "dividends": ("Looks at the dividend track record: is it paid consistently, "
                  "growing, and affordable for the company? Reliable, growing "
                  "dividends signal a stable business. Companies that pay none "
                  "are treated as neutral."),
    "short_interest": ("How heavily traders overall are betting against the stock. "
                       "Heavy or rising bets against it are bearish; light or "
                       "falling is bullish. Higher = less negative positioning."),
    "insider": ("Tracks buying and selling by the company's own executives (their "
                "official Form 4 filings). Insiders buying is a confidence signal; "
                "selling is weighed more lightly. Higher = insiders leaning "
                "bullish."),
    "liquidity": ("Measures whether the company makes enough cash to cover its "
                  "short-term bills — its operating and free cash flow versus what "
                  "it owes soon. Higher = a stronger cash cushion and less "
                  "financial stress."),
    "sma_crossover": ("A simple trend confirmation: is the 20-day average price "
                      "above the 50-day? Fast-above-slow means short-term momentum "
                      "is beating the medium-term trend. Higher = bullish "
                      "crossover."),
    "news": ("Reads recent news articles about the company and scores their tone "
             "as positive, neutral or negative. More upbeat coverage nudges the "
             "score up. Higher = friendlier recent news."),
}


def signal_plain(name: str) -> str | None:
    """Retail-friendly, plain-English explanation of what a signal is."""
    return SIGNAL_PLAIN.get(name)


def signal_description(name: str) -> str | None:
    """Plain-English description of how a signal's rating is computed."""
    return SIGNAL_DESCRIPTIONS.get(name)


def is_scored(x) -> bool:
    """True for a finite numeric score usable in composites and tables."""
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def insider_to_ten(signal: str | None) -> float | None:
    """Map cosmo's BULLISH/BEARISH/NEUTRAL onto the shared 1-10 scale."""
    return INSIDER_SIGNAL_TEN.get((signal or "").upper())


def to_ten(value: float | None, lo: float, hi: float) -> float | None:
    """Map a value in [lo, hi] onto [1, 10] (clamped, one decimal)."""
    if value is None or hi == lo:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    t = (value - lo) / (hi - lo) * 9.0 + 1.0
    return round(max(1.0, min(10.0, t)), 1)


def ten_to_label(ten: float | None) -> str:
    """Canonical house rating for a 1-10 score.

    This is the SINGLE vocabulary shown to users everywhere (per-signal ratings,
    composite, recommender), so every factor reads the same way regardless of
    each author's own wording. Authors' native labels are kept only in the
    per-signal breakdown for reference.
    """
    if ten is None:
        return "N/A"
    if ten >= 8.0:
        return "STRONG BUY"
    if ten >= 6.5:
        return "BUY"
    if ten >= 4.5:
        return "HOLD"
    if ten >= 3.0:
        return "SELL"
    return "STRONG SELL"


def fmt_ten(ten: float | None) -> str:
    return f"{ten:.1f}/10" if is_scored(ten) else "—"
