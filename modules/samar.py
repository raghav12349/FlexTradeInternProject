#!/usr/bin/env python3
"""
Multi-Window Momentum Strength Screener (grouped-endpoint version)
═══════════════════════════════════════════════════════════════════════════
Choose an index → uses massive.com's GROUPED daily endpoint to pull
market-wide closing prices for FOUR dates only:
  - ~252 trading days ago  ("12 months ago")
  - ~126 trading days ago  ("6 months ago")
  - ~63  trading days ago  ("3 months ago")
  - ~21  trading days ago  ("1 month ago", recent month excluded)

That's 4 API calls TOTAL covering the entire US market.

Three momentum windows per ticker:
  - momentum_12_1 = (price_21 / price_252) - 1
  - momentum_6_1  = (price_21 / price_126) - 1
  - momentum_3_1  = (price_21 / price_63)  - 1

A stock is only "strong" if ALL THREE windows agree (all above a z-score
floor) -- a stock with a great 12-1 number but a stalled/negative 6-1 or 3-1
had its gain front-loaded a year ago and isn't currently strong. This
multi-window check is a robustness filter on top of the single-window signal.

Standardization uses MEDIAN / MAD (median absolute deviation), not
mean/stdev -- robust to single extreme outliers (e.g. a stock with a
data-quality price discontinuity) without needing an arbitrary hard cutoff.

Combined z-score = min(z_12_1, z_6_1, z_3_1)  -- "as strong as its weakest window".
1-10 score = percentile rank of the combined z-score within the index.

Output: prints TOP 10 (or single stock), saves full ranked list to
output/momentum_{INDEX}.csv
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import csv
import time
import os
import re
import statistics
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ────────────────────────────────────────────────────────────────────
# API key lives in config.py (gitignored) or the MASSIVE_API_KEY env var --
# never hardcoded here so this file is safe to share/commit.
try:
    from config import API_KEY
except ImportError:
    API_KEY = os.environ.get("MASSIVE_API_KEY")
    if not API_KEY:
        raise RuntimeError(
            "No API key found. Create config.py with API_KEY = \"...\" "
            "(see config.py.example) or set the MASSIVE_API_KEY env var."
        )
BASE_URL = "https://api.massive.com"
HERE     = os.path.dirname(os.path.abspath(__file__))
OUT_DIR  = os.path.join(HERE, "output")
FIN_CACHE_DIR          = os.path.join(HERE, ".cache", "financials")
FIN_CACHE_MAX_AGE_DAYS = 90

# Calendar-day offsets used to LOCATE each trading-day snapshot.
TRADING_DAYS_252_CAL = 370   # ~252 trading days ≈ 370 calendar days back
TRADING_DAYS_126_CAL = 185   # ~126 trading days ≈ 185 calendar days back
TRADING_DAYS_63_CAL  = 93    # ~63  trading days ≈ 93  calendar days back
TRADING_DAYS_21_CAL  = 30    # ~21  trading days ≈ 30  calendar days back

# Expected calendar-day GAPS between snapshots, for drift sanity checks.
EXPECTED_GAP_252_21 = TRADING_DAYS_252_CAL - TRADING_DAYS_21_CAL   # ~340
EXPECTED_GAP_126_21 = TRADING_DAYS_126_CAL - TRADING_DAYS_21_CAL   # ~155
EXPECTED_GAP_63_21  = TRADING_DAYS_63_CAL  - TRADING_DAYS_21_CAL   # ~63
GAP_DRIFT_TOLERANCE = 5  # calendar days

SUSPECT_THRESHOLD = 1.0   # exclude any single-window momentum > +-100%
MIN_STRENGTH_Z    = 0.5   # all three windows' z-scores must exceed this to be "strong"

TOP_N = 10

# A fixed, sector-diverse basket (~110 large/mid caps across all 11 GICS
# sectors) used as a comparison universe for an arbitrary single ticker --
# independent of any index, so a stock not in the S&P 500 or Dow can still
# be scored against "the broad market."
DIVERSE_UNIVERSE = {
    # Information Technology
    "AAPL": "Information Technology", "MSFT": "Information Technology", "NVDA": "Information Technology",
    "ADBE": "Information Technology", "CRM": "Information Technology", "ORCL": "Information Technology",
    "AMD": "Information Technology", "INTC": "Information Technology", "CSCO": "Information Technology",
    "IBM": "Information Technology",
    # Health Care
    "UNH": "Health Care", "JNJ": "Health Care", "LLY": "Health Care", "PFE": "Health Care",
    "ABBV": "Health Care", "MRK": "Health Care", "TMO": "Health Care", "ABT": "Health Care",
    "DHR": "Health Care", "BMY": "Health Care",
    # Financials
    "JPM": "Financials", "BAC": "Financials", "WFC": "Financials", "GS": "Financials",
    "MS": "Financials", "BLK": "Financials", "AXP": "Financials", "C": "Financials",
    "SCHW": "Financials", "USB": "Financials",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "NKE": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "LOW": "Consumer Discretionary", "BKNG": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    "MAR": "Consumer Discretionary",
    # Communication Services
    "GOOGL": "Communication Services", "META": "Communication Services", "NFLX": "Communication Services",
    "DIS": "Communication Services", "CMCSA": "Communication Services", "T": "Communication Services",
    "VZ": "Communication Services", "TMUS": "Communication Services", "EA": "Communication Services",
    "WBD": "Communication Services",
    # Industrials
    "HON": "Industrials", "UPS": "Industrials", "CAT": "Industrials", "BA": "Industrials",
    "GE": "Industrials", "LMT": "Industrials", "RTX": "Industrials", "DE": "Industrials",
    "UNP": "Industrials", "MMM": "Industrials",
    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples", "WMT": "Consumer Staples",
    "COST": "Consumer Staples", "MDLZ": "Consumer Staples", "CL": "Consumer Staples", "KMB": "Consumer Staples",
    "GIS": "Consumer Staples", "STZ": "Consumer Staples",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "SLB": "Energy", "EOG": "Energy",
    "MPC": "Energy", "PSX": "Energy", "OXY": "Energy", "VLO": "Energy", "WMB": "Energy",
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities", "AEP": "Utilities", "EXC": "Utilities",
    "SRE": "Utilities", "D": "Utilities", "PEG": "Utilities", "ED": "Utilities", "XEL": "Utilities",
    # Real Estate
    "PLD": "Real Estate", "AMT": "Real Estate", "EQIX": "Real Estate", "SPG": "Real Estate",
    "PSA": "Real Estate", "O": "Real Estate", "DLR": "Real Estate", "WELL": "Real Estate",
    "AVB": "Real Estate", "EQR": "Real Estate",
    # Materials
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials", "ECL": "Materials", "NEM": "Materials",
    "FCX": "Materials", "DOW": "Materials", "NUE": "Materials", "PPG": "Materials", "ALB": "Materials",
}

# Shape "quality" ordering -- used only as a secondary tiebreaker / display
# aid, NEVER to override z_combined ranking. Reflects: is momentum currently
# building (good) or fading (bad), independent of its absolute level.
#   ACCELERATING > DIP > FLAT/MIXED > HUMP > DECELERATING
SHAPE_RANK = {
    "ACCELERATING": 4,
    "DIP": 3,
    "FLAT": 2,
    "MIXED": 2,
    "HUMP": 1,
    "DECELERATING": 0,
}

# Each entry: (display name, Wikipedia URL, short_code for CSV filename,
# cap_tier). cap_tier is the crude, free, index-membership-based size
# classification used for CapZ (see build_cap_tiers() below).
INDEXES = {
    "A": ("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "sp500", "Large"),
    "B": ("Dow Jones Industrial Average", "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", "dowjones", "Large"),
    "C": ("Nasdaq-100", "https://en.wikipedia.org/wiki/Nasdaq-100", "nasdaq100", "Large"),
    "D": ("S&P MidCap 400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "sp400", "Mid"),
    "E": ("S&P SmallCap 600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "sp600", "Small"),
    "F": ("S&P 100", "https://en.wikipedia.org/wiki/S%26P_100", "sp100", "Large"),
    "G": ("Dow Jones Transportation Average", "https://en.wikipedia.org/wiki/Dow_Jones_Transportation_Average", "dowtransport", "Large"),
    "H": ("Dow Jones Utility Average", "https://en.wikipedia.org/wiki/Dow_Jones_Utility_Average", "dowutility", "Large"),
    "I": ("Russell 1000", "https://en.wikipedia.org/wiki/Russell_1000_Index", "russell1000", "Large"),
    "J": ("S&P 500 Dividend Aristocrats", "https://en.wikipedia.org/wiki/S%26P_500_Dividend_Aristocrats", "divaristocrats", "Large"),
}

# Custom-index submenu (option 8) offers only the indexes NOT already on the
# top-level menu (A-E), so there's no overlap/duplication.
CUSTOM_INDEX_KEYS = ["F", "G", "H", "I", "J"]

# Top-level menu options 1-5 map to these indexes directly.
TOP_MENU_INDEXES = {"1": "A", "2": "B", "3": "C", "4": "D", "5": "E"}

# GICS sectors -- used by the generic Wikipedia scraper to auto-detect which
# table column holds the sector (column order varies between index pages).
KNOWN_SECTORS = {
    "Information Technology", "Health Care", "Financials", "Consumer Discretionary",
    "Communication Services", "Industrials", "Consumer Staples", "Energy",
    "Utilities", "Real Estate", "Materials",
}

# Some pages (e.g. Nasdaq-100) classify by ICB ("Industry Classification
# Benchmark") instead of GICS -- different label set for largely the same
# groupings. Map ICB labels onto the closest GICS sector so they're
# recognized as a sector column and standardized consistently across indexes.
ICB_TO_GICS = {
    "Technology": "Information Technology",
    "Telecommunications": "Communication Services",
    "Health Care": "Health Care",
    "Healthcare": "Health Care",
    "Financials": "Financials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Basic Materials": "Materials",
    "Materials": "Materials",
}
KNOWN_SECTORS_RAW = KNOWN_SECTORS | set(ICB_TO_GICS.keys())

# ── Helper: find nearest trading day with grouped data, walking backward ──────
def get_grouped_data(target_date, max_back=10):
    d = target_date
    for _ in range(max_back):
        url = f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}?adjusted=true&apiKey={API_KEY}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
            if data.get("resultsCount", 0) > 0:
                return d, {r["T"]: r["c"] for r in data["results"]}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5)
                continue
        d = d - timedelta(days=1)
    return None, None

# ── Step 1: Get index constituents from Wikipedia ──────────────────────────────
# Real market-cap cutoffs (USD) for classifying an arbitrary ticker that
# isn't a member of any index we scrape. Roughly: S&P 500 floor is ~$15-20B
# these days, S&P MidCap 400 floor ~$2B, below that is small/micro-cap.
# Used ONLY for tickers outside the chosen universe -- the universe's own
# members keep using the free index-membership proxy (build_cap_tiers),
# since fetching real market cap for hundreds of tickers per run is too many
# extra API calls to be worth it.
CAP_TIER_LARGE_FLOOR = 10e9   # >= $10B  -> Large
CAP_TIER_MID_FLOOR   = 2e9    # >= $2B   -> Mid, else Small

_MARKET_CAP_CACHE = {}

def get_market_cap(ticker):
    """Real market cap (USD) for a single ticker via /v3/reference/tickers/{ticker}.
    Returns None on any failure (bad ticker, rate limit, etc.) -- callers
    must handle that by falling back to the index-membership proxy.
    """
    if ticker in _MARKET_CAP_CACHE:
        return _MARKET_CAP_CACHE[ticker]
    url = f"{BASE_URL}/v3/reference/tickers/{ticker}?apiKey={API_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
        cap = data.get("results", {}).get("market_cap")
        _MARKET_CAP_CACHE[ticker] = cap
        return cap
    except Exception:
        _MARKET_CAP_CACHE[ticker] = None
        return None

def classify_cap_tier(market_cap):
    """Real-market-cap-based Large/Mid/Small classification, using the
    cutoffs above. Returns None if market_cap is None/0."""
    if not market_cap:
        return None
    if market_cap >= CAP_TIER_LARGE_FLOOR:
        return "Large"
    if market_cap >= CAP_TIER_MID_FLOOR:
        return "Mid"
    return "Small"


# ── Sector fallback via SIC code ─────────────────────────────────────────────
# The Wikipedia scrapes only label index constituents, and DIVERSE_UNIVERSE is
# a hand-labelled list -- any other ticker used to fall through to "Unknown",
# which kills every sector-relative computation for it. Fallback: pull the SEC
# SIC code from /v3/reference/tickers/{ticker} (same endpoint as
# get_market_cap) and map it onto the GICS sector labels the rest of the
# script uses. Disk-cached, so each ticker costs at most one API call ever.
SECTOR_CACHE_PATH = os.path.join(HERE, ".cache", "sectors.json")
_SECTOR_API_CACHE = None

# Ordered SIC ranges -> GICS sector; first match wins, specific ranges first.
_SIC_TO_GICS = [
    (2830, 2836, "Health Care"),             # pharma preparations
    (3841, 3851, "Health Care"),             # surgical/medical instruments
    (8011, 8099, "Health Care"),             # hospitals & health services
    (7370, 7379, "Information Technology"),  # software & data processing
    (3670, 3699, "Information Technology"),  # electronic components
    (3570, 3579, "Information Technology"),  # computers & office equipment
    (3661, 3669, "Communication Services"),  # telephone/broadcast equipment
    (4810, 4899, "Communication Services"),  # telecom services
    (7800, 7841, "Communication Services"),  # movies & entertainment
    (4900, 4991, "Utilities"),
    (6500, 6552, "Real Estate"),
    (6798, 6798, "Real Estate"),             # REITs
    (6000, 6411, "Financials"),              # banks, brokers, insurance
    (6553, 6799, "Financials"),              # holding/investment offices
    (1311, 1389, "Energy"),                  # oil & gas
    (2911, 2911, "Energy"),                  # petroleum refining
    (5400, 5499, "Consumer Staples"),        # food stores
    (5900, 5963, "Consumer Staples"),        # drug & grocery retail
    (2000, 2199, "Consumer Staples"),        # food & tobacco manufacturing
    (2800, 2999, "Materials"),               # chemicals (pharma caught above)
    (1000, 1499, "Materials"),               # mining
    (2400, 2499, "Materials"),               # lumber & wood
    (2600, 2699, "Materials"),               # paper
    (3300, 3399, "Materials"),               # primary metals
    (3400, 3499, "Industrials"),             # fabricated metal
    (3500, 3569, "Industrials"),             # industrial machinery
    (3700, 3769, "Consumer Discretionary"),  # motor vehicles
    (5000, 5999, "Consumer Discretionary"),  # retail & wholesale trade
    (7000, 7399, "Consumer Discretionary"),  # hotels, amusement, services
    (4000, 4799, "Industrials"),             # transportation
    (7400, 8999, "Industrials"),             # business & engineering services
]

# Tickers whose SEC SIC code lands in the wrong GICS sector.
_SECTOR_OVERRIDES = {
    "META": "Communication Services", "GOOGL": "Communication Services",
    "GOOG": "Communication Services", "NFLX": "Communication Services",
    "DIS":  "Communication Services", "SNAP": "Communication Services",
    "AMZN": "Consumer Discretionary", "EBAY": "Consumer Discretionary",
    "V": "Financials", "MA": "Financials", "PYPL": "Financials",
    "MARA": "Information Technology", "RIOT": "Information Technology",
    "CLSK": "Information Technology",
}


def _sic_to_gics(sic):
    for lo, hi, sector in _SIC_TO_GICS:
        if lo <= sic <= hi:
            return sector
    return None


def get_sector_api(ticker, retries=3):
    """GICS-style sector for any ticker via its SEC SIC code. Returns None
    only when the API has no SIC code for the ticker (rare: SPACs, some ADRs).
    Failures are not cached, so a rate-limit blip doesn't stick."""
    global _SECTOR_API_CACHE
    if ticker in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[ticker]
    if _SECTOR_API_CACHE is None:
        try:
            with open(SECTOR_CACHE_PATH) as f:
                _SECTOR_API_CACHE = json.load(f)
        except Exception:
            _SECTOR_API_CACHE = {}
    if ticker in _SECTOR_API_CACHE:
        return _SECTOR_API_CACHE[ticker]
    url = f"{BASE_URL}/v3/reference/tickers/{ticker}?apiKey={API_KEY}"
    sector = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.load(resp)
            res = data.get("results", {})
            sic = res.get("sic_code")
            sector = _sic_to_gics(int(sic)) if sic else None
            if res.get("market_cap"):
                _MARKET_CAP_CACHE.setdefault(ticker, res["market_cap"])
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    else:
        return None
    _SECTOR_API_CACHE[ticker] = sector
    try:
        os.makedirs(os.path.dirname(SECTOR_CACHE_PATH), exist_ok=True)
        with open(SECTOR_CACHE_PATH, "w") as f:
            json.dump(_SECTOR_API_CACHE, f)
    except Exception:
        pass
    return sector


def fill_unknown_sectors(tickers, sectors):
    """Resolve any 'Unknown' sector labels in-place via the SIC fallback.
    Throttled on cache misses only -- a warm cache costs zero API calls.
    Returns the same dict."""
    unknown = [t for t in tickers if sectors.get(t, "Unknown") == "Unknown"]
    filled = 0
    for t in unknown:
        cached = (t in _SECTOR_OVERRIDES
                  or (_SECTOR_API_CACHE is not None and t in _SECTOR_API_CACHE))
        sec = get_sector_api(t)
        if sec:
            sectors[t] = sec
            filled += 1
        if not cached:
            time.sleep(0.05)
    if unknown:
        print(f"  Sector fallback (SIC): resolved {filled}/{len(unknown)} unlabeled tickers")
    return sectors

# ── Market regime filter (S&P 500 vs its 200-day moving average) ─────────────
# Momentum strategies' catastrophic losses cluster when the broad market is
# below its long-term trend (bear markets and their violent rebounds). Gate:
# when SPY < its 200-day SMA, BUY/STRONG BUY labels are capped to "HOLD*".
# Ranking, z-scores, and 1-10/3F scores are NEVER altered -- this caps the
# action label only. Adopted on external trend-following evidence, not our
# backtest (our window is too short to contain enough bear markets to test
# a timing rule). Known cost: lags at recoveries (whipsaw).
REGIME_LOOKBACK_CAL_DAYS = 320   # ~220 trading days of SPY closes
REGIME_MA_WINDOW         = 200

_REGIME_CACHE = "unset"   # sentinel: None is a valid cached result (fetch failed)

def get_market_regime(retries=3):
    """Is SPY above its 200-day simple moving average?
    Returns {"risk_on": bool, "spy": float, "ma200": float, "date": str},
    or None if SPY data couldn't be fetched -- callers must FAIL OPEN
    (rank and label as usual, just don't cap)."""
    global _REGIME_CACHE
    if _REGIME_CACHE != "unset":
        return _REGIME_CACHE
    frm = (date.today() - timedelta(days=REGIME_LOOKBACK_CAL_DAYS)).isoformat()
    to  = date.today().isoformat()
    url = (f"{BASE_URL}/v2/aggs/ticker/SPY/range/1/day/{frm}/{to}"
           f"?adjusted=true&sort=asc&limit=500&apiKey={API_KEY}")
    regime = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
            bars = data.get("results") or []
            closes = [b["c"] for b in bars if b.get("c")]
            if len(closes) >= REGIME_MA_WINDOW:
                ma  = sum(closes[-REGIME_MA_WINDOW:]) / REGIME_MA_WINDOW
                spy = closes[-1]
                regime = {"risk_on": spy > ma, "spy": spy, "ma200": ma,
                          "date": date.fromtimestamp(bars[-1]["t"] / 1000).isoformat()}
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            break
        except Exception:
            break
    _REGIME_CACHE = regime
    return regime


def print_regime(regime):
    if regime is None:
        print("\n  ⚠ Market regime: UNAVAILABLE (SPY fetch failed) -- "
              "buy signals NOT capped this run.")
    elif regime["risk_on"]:
        print(f"\n  Market regime: RISK-ON  (SPY {regime['spy']:.0f} > 200d MA "
              f"{regime['ma200']:.0f}, as of {regime['date']}) -- signals uncapped.")
    else:
        print(f"\n  Market regime: RISK-OFF (SPY {regime['spy']:.0f} < 200d MA "
              f"{regime['ma200']:.0f}, as of {regime['date']}) -- "
              f"BUY/STRONG BUY capped to HOLD*.")


def apply_regime_cap(results, regime):
    """When risk-off, downgrade BUY/STRONG BUY -> 'HOLD*' on both the
    momentum and 3F labels, in-place. Scores/z's/rankings untouched.
    No-op when risk-on or when regime data is unavailable (fail open)."""
    if regime is None or regime["risk_on"]:
        return
    for v in results.values():
        if v.get("recommendation") in ("STRONG BUY", "BUY"):
            v["recommendation"] = "HOLD*"
        if v.get("rec_3f") in ("STRONG BUY", "BUY"):
            v["rec_3f"] = "HOLD*"


REGIME_CAP_NOTE = ("  * = capped from BUY/STRONG BUY: market is below its "
                   "200-day MA, where momentum signals historically fail hardest.")


_CAP_TIER_CACHE = None

def build_cap_tiers():
    """Build a {ticker: 'Large'/'Mid'/'Small'} map from index membership --
    the free, no-extra-API-calls cap-size proxy discussed earlier. Pulls
    S&P 500 (Large), S&P MidCap 400 (Mid), S&P SmallCap 600 (Small), and
    Nasdaq-100 (Large) constituent lists from Wikipedia (cheap HTML fetches,
    NOT massive.com calls -- doesn't count against API usage).

    Cached in-process so repeated runs in one session don't re-fetch.
    Falls back to "Large" for anything not found in any of these lists
    (e.g. DIVERSE_UNIVERSE extras, Dow Transports/Utilities names).

    [Honesty note]: this is a CRUDE proxy, not real market cap. S&P 500
    spans ~$10B to $3T+. Use CapZ as a rough "is this stock large/mid/small
    relative to peers in the same tier" signal, not a precise figure.
    """
    global _CAP_TIER_CACHE
    if _CAP_TIER_CACHE is not None:
        return _CAP_TIER_CACHE

    tiers = {}
    # Order matters: process Small/Mid first so a ticker that (rarely)
    # appears in multiple lists doesn't get overwritten by a later "Large"
    # default via DIVERSE_UNIVERSE -- but S&P 500/Nasdaq still take final
    # priority for genuinely large names via setdefault below.
    for key, label in (("E", "Small"), ("D", "Mid"), ("A", "Large"), ("C", "Large")):
        try:
            tks, _nm, _secs = get_constituents(key)
            for t in tks:
                tiers[t] = label
        except Exception as e:
            print(f"  ⚠ Could not fetch {INDEXES[key][0]} for cap-tier mapping ({e})")
    for t in DIVERSE_UNIVERSE:
        tiers.setdefault(t, "Large")

    _CAP_TIER_CACHE = tiers
    return tiers

# Fallback GICS sector for indexes whose Wikipedia table has no sector
# column -- these indexes are single-sector by definition, so we can label
# every constituent directly rather than leaving them all "Unknown" (which
# made SecZ always "--" for these indexes).
INDEX_FALLBACK_SECTOR = {
    "G": "Industrials",   # Dow Jones Transportation Average
    "H": "Utilities",     # Dow Jones Utility Average
}

# Single-sector indexes -- SecZ within these is mathematically near-identical
# to z_combined (the whole universe IS the "sector"), so it's not a useful
# diversification check. We still compute it (no harm), but the run() output
# notes this so users don't read meaning into a redundant number.
SINGLE_SECTOR_INDEXES = {"G", "H"}

# Roughly-expected constituent counts per index, for sanity-checking the
# Wikipedia scrape. If a scrape returns a count outside [lo, hi], the page
# format likely changed and the scraped columns may be wrong -- warn loudly
# rather than silently using bad data.
EXPECTED_COUNTS = {
    "A": (480, 520),   # S&P 500
    "B": (28, 32),     # Dow 30
    "C": (95, 105),    # Nasdaq-100
    "D": (380, 420),   # S&P MidCap 400
    "E": (580, 620),   # S&P SmallCap 600
    "F": (95, 105),    # S&P 100
    "G": (18, 22),     # Dow Transports (20)
    "H": (13, 17),     # Dow Utilities (15)
    "I": (950, 1050),  # Russell 1000
    "J": (60, 75),     # Dividend Aristocrats
}

# Local cache of scraped constituents (tickers + sectors), so a Wikipedia
# outage or HTML-format change doesn't hard-fail every run -- falls back to
# the last good scrape, with a clear warning that data may be stale.
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE_MAX_AGE_DAYS = 30

def _cache_path(index_key):
    return os.path.join(CACHE_DIR, f"constituents_{index_key}.json")

def _load_constituents_cache(index_key):
    path = _cache_path(index_key)
    try:
        with open(path) as f:
            data = json.load(f)
        fetched = data.get("fetched")
        if fetched:
            try:
                age_days = (date.today() - date.fromisoformat(fetched)).days
                if age_days > CACHE_MAX_AGE_DAYS:
                    print(f"  ⚠ Cached copy is {age_days} days old (> {CACHE_MAX_AGE_DAYS}-day limit) -- using anyway since live scrape failed.")
            except Exception:
                pass
        return data["tickers"], data["sectors"], fetched
    except Exception:
        return None

def _save_constituents_cache(index_key, tickers, sectors):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(index_key), "w") as f:
            json.dump({"tickers": tickers, "sectors": sectors,
                       "fetched": date.today().isoformat()}, f)
    except Exception:
        pass  # caching is best-effort; never fail the run over it

def get_constituents(index_key):
    name, wiki_url, _short, _cap = INDEXES[index_key]

    try:
        tickers, nm, secs = get_constituents_from_url(wiki_url, name)
        if not tickers:
            raise RuntimeError("scrape returned 0 tickers")
    except Exception as e:
        print(f"  ⚠ Live scrape failed ({e}) -- trying cached copy...")
        cached = _load_constituents_cache(index_key)
        if cached is None:
            raise RuntimeError(f"Could not fetch {name} (live scrape failed and no cache available)")
        tickers, secs, fetched = cached
        print(f"  Using cached {name} constituents from {fetched} ({len(tickers)} tickers)")
        return tickers, name, secs

    # Sanity-check the count against expectations -- catches silent
    # table-format changes (e.g. wrong table picked, rowspan misparse).
    lo, hi = EXPECTED_COUNTS.get(index_key, (0, 10**9))
    if not (lo <= len(tickers) <= hi):
        print(f"  ⚠ {name}: scraped {len(tickers)} tickers, expected ~{lo}-{hi}. "
              f"Wikipedia's table format may have changed -- results could be wrong.")

    fallback = INDEX_FALLBACK_SECTOR.get(index_key)
    if fallback and (not secs or all(v == "Unknown" for v in secs.values())):
        secs = {t: fallback for t in tickers}

    _save_constituents_cache(index_key, tickers, secs)
    return tickers, nm, secs

def get_constituents_from_url(wiki_url, name):
    print(f"\n{'─'*60}")
    print(f"STEP 1 — Fetching constituents: {name}")
    print(f"{'─'*60}")

    req = urllib.request.Request(wiki_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    EXCHANGES = {"NYSE", "NASDAQ"}
    TICKER_RE = re.compile(r'^[A-Z][A-Z.\-]{0,5}$')

    def parse_table(table_html):
        """Returns (rows_cells, best_col, best_score).

        Handles rowspan: Wikipedia tables (e.g. Nasdaq-100's GICS Sector
        column) often merge several consecutive rows' sector cells into one
        <td rowspan="N">. A naive per-row <td> scan only sees that cell on
        the FIRST of those N rows, so every subsequent row is missing a
        column and everything shifts left -- silently corrupting the sector
        (and potentially ticker) column for ~half the table. We track
        "pending" rowspan cells and re-insert them into the following rows
        at the correct column position.
        """
        rows_cells = []
        pending = {}  # col_index -> [text, remaining_rows]
        for row_match in re.finditer(r'<tr[^>]*>(.*?)</tr>', table_html, re.S):
            row_html = row_match.group(1)
            new_cells = []
            for cm in re.finditer(r'<td([^>]*)>(.*?)</td>', row_html, re.S):
                attrs, c = cm.group(1), cm.group(2)
                rs_m = re.search(r'rowspan\s*=\s*"?(\d+)"?', attrs)
                rowspan = int(rs_m.group(1)) if rs_m else 1
                link = re.search(r'<a[^>]*>([^<]*)</a>', c)
                text = link.group(1) if link else re.sub(r'<[^>]+>', '', c)
                new_cells.append((text.strip(), rowspan))

            if not new_cells and not pending:
                continue  # header row or empty row

            total_cols = len(new_cells) + len(pending)
            row = []
            new_idx = 0
            for col in range(total_cols):
                if col in pending:
                    text, rem = pending[col]
                    row.append(text)
                    if rem - 1 <= 0:
                        del pending[col]
                    else:
                        pending[col] = (text, rem - 1)
                elif new_idx < len(new_cells):
                    text, rowspan = new_cells[new_idx]
                    new_idx += 1
                    row.append(text)
                    if rowspan > 1:
                        pending[col] = (text, rowspan - 1)
            if row:
                rows_cells.append(row)

        if not rows_cells:
            return [], 0, -1
        n_cols = max(len(r) for r in rows_cells)
        best_col, best_score = 0, -1
        for col in range(n_cols):
            score = sum(1 for r in rows_cells if col < len(r)
                         and TICKER_RE.match(r[col]) and r[col] not in EXCHANGES)
            if score > best_score:
                best_col, best_score = col, score
        return rows_cells, best_col, best_score

    # A page can contain several "wikitable" tables (e.g. a "related indices"
    # table alongside the actual constituents table). Try id="constituents"
    # first, but pages often have a SECOND wikitable too (e.g. a "Selected
    # changes" history table) which can have MORE rows that still look like
    # tickers, so raw score alone picks the wrong table. Instead: among
    # tables where the best column is "almost entirely tickers" (ratio >=
    # CLEAN_RATIO -- i.e. this column basically IS a ticker column, not just
    # has many tickers scattered in it), take the FIRST such table in
    # document order. Only if none qualifies, fall back to raw best score.
    CLEAN_RATIO = 0.95
    candidates = []
    m = re.search(r'<table id="constituents".*?</table>', html, re.S)
    if m:
        candidates.append(m.group(0))
    candidates += re.findall(r'<table[^>]*class="[^"]*\bwikitable\b[^"]*".*?</table>', html, re.S)

    rows_cells, best_col, best_score = [], 0, -1
    fallback_rows, fallback_col, fallback_score = [], 0, -1
    for table_html in candidates:
        rc, bc, bs = parse_table(table_html)
        if bs > fallback_score:
            fallback_rows, fallback_col, fallback_score = rc, bc, bs
        if rc and bs / len(rc) >= CLEAN_RATIO:
            rows_cells, best_col, best_score = rc, bc, bs
            break

    if not rows_cells:
        rows_cells, best_col, best_score = fallback_rows, fallback_col, fallback_score

    if not rows_cells:
        print("  Found 0 tickers (no usable table found on this page)")
        return [], name, {}

    n_cols = max(len(r) for r in rows_cells)

    # Detect sector column: the column whose values most often match a
    # known GICS sector name. -1 if no such column exists on this page.
    sector_col, sector_score = -1, 0
    for col in range(n_cols):
        score = sum(1 for r in rows_cells if col < len(r) and r[col] in KNOWN_SECTORS_RAW)
        if score > sector_score:
            sector_col, sector_score = col, score

    tickers = []
    sectors = {}
    for r in rows_cells:
        if best_col >= len(r):
            continue
        sym = r[best_col]
        if not TICKER_RE.match(sym) or sym in EXCHANGES:
            continue
        norm = sym.replace(".", "-")
        tickers.append(norm)
        if sector_col >= 0 and sector_col < len(r) and r[sector_col] in KNOWN_SECTORS_RAW:
            raw = r[sector_col]
            sectors[norm] = ICB_TO_GICS.get(raw, raw)
        else:
            sectors[norm] = "Unknown"

    tickers = sorted(set(tickers))
    print(f"  Found {len(tickers)} tickers"
          + (f" (sectors detected)" if sector_col >= 0 else " (no sector column found)"))
    return tickers, name, sectors

# ── Step 2: Pull market-wide prices for 4 dates (4 API calls) ────────────────
def get_market_snapshots():
    today = date.today()

    print(f"\n{'─'*60}")
    print(f"STEP 2 — Pulling market-wide prices (4 API calls total)")
    print(f"{'─'*60}")

    target_252 = today - timedelta(days=TRADING_DAYS_252_CAL)
    date_252, prices_252 = get_grouped_data(target_252)
    print(f"  ~12 months ago  → {date_252}  ({len(prices_252) if prices_252 else 0} tickers)")

    target_126 = today - timedelta(days=TRADING_DAYS_126_CAL)
    date_126, prices_126 = get_grouped_data(target_126)
    print(f"  ~6 months ago   → {date_126}  ({len(prices_126) if prices_126 else 0} tickers)")

    target_63 = today - timedelta(days=TRADING_DAYS_63_CAL)
    date_63, prices_63 = get_grouped_data(target_63)
    print(f"  ~3 months ago   → {date_63}  ({len(prices_63) if prices_63 else 0} tickers)")

    target_21 = today - timedelta(days=TRADING_DAYS_21_CAL)
    date_21, prices_21 = get_grouped_data(target_21)
    print(f"  ~1 month ago    → {date_21}  ({len(prices_21) if prices_21 else 0} tickers)")

    if not prices_252 or not prices_126 or not prices_63 or not prices_21:
        raise RuntimeError("Could not retrieve grouped market data for one or more dates.")

    # Sanity check: each snapshot independently walks backward if it lands on
    # a holiday/weekend, so actual gaps can drift from the intended values.
    # A large drift means a window no longer measures what it claims to.
    gap_252_21 = (date_21 - date_252).days
    gap_126_21 = (date_21 - date_126).days
    gap_63_21  = (date_21 - date_63).days
    drift_252 = abs(gap_252_21 - EXPECTED_GAP_252_21)
    drift_126 = abs(gap_126_21 - EXPECTED_GAP_126_21)
    drift_63  = abs(gap_63_21  - EXPECTED_GAP_63_21)
    print(f"  12-1 window gap : {gap_252_21} cal days (expected ~{EXPECTED_GAP_252_21})")
    print(f"  6-1  window gap : {gap_126_21} cal days (expected ~{EXPECTED_GAP_126_21})")
    print(f"  3-1  window gap : {gap_63_21} cal days (expected ~{EXPECTED_GAP_63_21})")
    if drift_252 > GAP_DRIFT_TOLERANCE:
        print(f"  ⚠ WARNING: 12-1 window drifted by {drift_252} days — "
              f"momentum_12_1 is skewed for ALL tickers this run.")
    if drift_126 > GAP_DRIFT_TOLERANCE:
        print(f"  ⚠ WARNING: 6-1 window drifted by {drift_126} days — "
              f"momentum_6_1 is skewed for ALL tickers this run.")
    if drift_63 > GAP_DRIFT_TOLERANCE:
        print(f"  ⚠ WARNING: 3-1 window drifted by {drift_63} days — "
              f"momentum_3_1 is skewed for ALL tickers this run.")

    return {
        "p252": prices_252, "p126": prices_126, "p63": prices_63, "p21": prices_21,
        "d252": date_252, "d126": date_126, "d63": date_63, "d21": date_21,
    }

# ── Step 3: Compute multi-window momentum ───────────────────────────────────────
def compute_momentum(tickers, snap):
    """Returns (momentum_dict, excluded_list).

    momentum_dict[ticker] = {"m12": ..., "m6": ..., "m3": ...}  (decimals, e.g. 0.30)
    A ticker is excluded entirely if ANY window is missing or suspect
    (>100% magnitude) -- a missing/bad value in one window makes the
    multi-window comparison meaningless for that ticker.
    """
    momentum = {}
    excluded = []
    for t in tickers:
        p252 = snap["p252"].get(t)
        p126 = snap["p126"].get(t)
        p63  = snap["p63"].get(t)
        p21  = snap["p21"].get(t)

        if (p252 is None or p126 is None or p63 is None or p21 is None
                or p252 == 0 or p126 == 0 or p63 == 0):
            excluded.append(f"{t} (missing data)")
            continue

        m12 = (p21 / p252) - 1
        m6  = (p21 / p126) - 1
        m3  = (p21 / p63) - 1

        if abs(m12) >= SUSPECT_THRESHOLD or abs(m6) >= SUSPECT_THRESHOLD or abs(m3) >= SUSPECT_THRESHOLD:
            excluded.append(f"{t} (SUSPECT >100% move)")
            continue

        momentum[t] = {"m12": m12, "m6": m6, "m3": m3}

    if excluded:
        print(f"\n  ⚠ Excluded {len(excluded)} ticker(s): "
              f"{', '.join(excluded[:15])}{' ...' if len(excluded) > 15 else ''}")

    return momentum, excluded

# ── Step 4: Standardize (median/MAD) + multi-window combination ───────────────
def robust_z(values, x, median=None, mad=None):
    """Median/MAD-based z-score. MAD scaled by 1.4826 so it's comparable
    to a standard deviation under a normal-distribution assumption, while
    being far less sensitive to a single extreme outlier than mean/stdev."""
    if median is None:
        median = statistics.median(values)
    if mad is None:
        mad = statistics.median(abs(v - median) for v in values) * 1.4826
    if mad == 0:
        return 0.0
    return (x - median) / mad

def term_structure_shape(z12, z6, z3):
    """Classify the 12-1 / 6-1 / 3-1 z-score sequence as a shape.

    Read left-to-right as time order: z12 (oldest window) -> z6 -> z3 (most
    recent). A "flat" tolerance avoids over-labeling tiny differences as a
    pattern.

    - FLAT          : all three within FLAT_TOL of each other
    - ACCELERATING  : monotonically increasing (z12 < z6 < z3) -- momentum
                       building, strongest most recently
    - DECELERATING  : monotonically decreasing (z12 > z6 > z3) -- momentum
                       fading, was strongest a year ago
    - HUMP          : z6 is the peak (z12 < z6 > z3) -- surged mid-period,
                       has since pulled back from that peak
    - DIP           : z6 is the trough (z12 > z6 < z3) -- stumbled
                       mid-period, has since recovered/reaccelerated
    """
    FLAT_TOL = 0.25
    spread = max(z12, z6, z3) - min(z12, z6, z3)
    if spread < FLAT_TOL:
        return "FLAT"
    if z12 < z6 < z3:
        return "ACCELERATING"
    if z12 > z6 > z3:
        return "DECELERATING"
    if z6 > z12 and z6 > z3:
        return "HUMP"
    if z6 < z12 and z6 < z3:
        return "DIP"
    return "MIXED"

def recommendation(z_combined, is_strong, shape_rank):
    """Buy-side-only call: STRONG BUY / BUY / HOLD / DON'T BUY.

    No sell ratings -- this screener only ever decides whether to add a
    position, hold off, or pass. Thresholds are judgment calls (same
    arbitrariness as MIN_STRENGTH_Z), not backtested cutoffs:
      - STRONG BUY : strong on all 3 windows, comfortably above the bar,
                      AND momentum currently building/recovering (shape_rank >= 3)
      - BUY        : strong on all 3 windows
      - HOLD       : not weak, but doesn't clear the "strong" bar
      - DON'T BUY  : meaningfully below the index on combined strength
    """
    if is_strong and z_combined >= 1.0 and shape_rank >= 3:
        return "STRONG BUY"
    if is_strong:
        return "BUY"
    if z_combined >= -0.25:
        return "HOLD"
    return "DON'T BUY"

def _med_mad(vals):
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals) * 1.4826
    return med, mad

SECTOR_MIN_GROUP = 5  # minimum stocks in a group (sector or cap-tier) before relative z is trusted

def _group_z_for_ticker(ticker, m, momentum_dict, group_of, min_group=SECTOR_MIN_GROUP, group_override=None):
    """Same idea as _group_relative_z but for a single ticker that is NOT
    part of momentum_dict (e.g. a custom ticker scored against an index it
    isn't a constituent of). Builds the group's median/MAD from momentum_dict
    members sharing the ticker's group label, then z-scores `m` against that.
    Returns None if the ticker's group is unknown or too small.

    group_override: if given, use this group label instead of looking the
    ticker up in group_of -- e.g. a real-market-cap-derived Large/Mid/Small
    tier for a ticker that isn't in any index's cap-tier map.
    """
    g = group_override if group_override is not None else group_of.get(ticker, "Unknown")
    if g == "Unknown" or g is None:
        return None
    members = [t for t in momentum_dict if group_of.get(t, "Unknown") == g]
    if len(members) < min_group:
        return None
    med12, mad12 = _med_mad([momentum_dict[t]["m12"] for t in members])
    med6,  mad6  = _med_mad([momentum_dict[t]["m6"]  for t in members])
    med3,  mad3  = _med_mad([momentum_dict[t]["m3"]  for t in members])
    z12 = robust_z(None, m["m12"], med12, mad12)
    z6  = robust_z(None, m["m6"],  med6,  mad6)
    z3  = robust_z(None, m["m3"],  med3,  mad3)
    return min(z3, z6, z12)

def _group_relative_z(momentum_dict, group_of, min_group=SECTOR_MIN_GROUP):
    """Generic helper: given a {ticker: group_label} map, compute per-group
    median/MAD for m12/m6/m3 (groups with >= min_group members only), then
    return {ticker: z_combined_within_group} (or None if ungrouped/too small).
    Used for both sector-relative and cap-tier-relative z -- same math,
    different grouping key, kept as one function rather than copy-pasting it.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for t in momentum_dict:
        g = group_of.get(t, "Unknown")
        if g != "Unknown":
            groups[g].append(t)

    group_stats = {}
    for g, members in groups.items():
        if len(members) >= min_group:
            s12 = _med_mad([momentum_dict[t]["m12"] for t in members])
            s6  = _med_mad([momentum_dict[t]["m6"]  for t in members])
            s3  = _med_mad([momentum_dict[t]["m3"]  for t in members])
            group_stats[g] = (s12, s6, s3)

    out = {}
    for t, v in momentum_dict.items():
        g = group_of.get(t, "Unknown")
        gs = group_stats.get(g)
        if gs is None:
            out[t] = None
            continue
        (med12, mad12), (med6, mad6), (med3, mad3) = gs
        z12 = robust_z(None, v["m12"], med12, mad12)
        z6  = robust_z(None, v["m6"],  med6,  mad6)
        z3  = robust_z(None, v["m3"],  med3,  mad3)
        out[t] = min(z3, z6, z12)
    return out

def standardize(momentum_dict, sectors=None, cap_tiers=None):
    m12_vals = [v["m12"] for v in momentum_dict.values()]
    m6_vals  = [v["m6"]  for v in momentum_dict.values()]
    m3_vals  = [v["m3"]  for v in momentum_dict.values()]

    med12, mad12 = _med_mad(m12_vals)
    med6,  mad6  = _med_mad(m6_vals)
    med3,  mad3  = _med_mad(m3_vals)

    # Sector-relative and cap-tier-relative z_combined -- "how strong vs.
    # peers in the same GICS sector / same market-cap tier", computed once
    # up front for all tickers (None where group unknown/too small).
    sector_z_map = _group_relative_z(momentum_dict, sectors) if sectors else {}
    cap_z_map = _group_relative_z(momentum_dict, cap_tiers) if cap_tiers else {}

    combined = {}
    for t, v in momentum_dict.items():
        z12 = robust_z(None, v["m12"], med12, mad12)
        z6  = robust_z(None, v["m6"],  med6,  mad6)
        z3  = robust_z(None, v["m3"],  med3,  mad3)

        # "As strong as its weakest window" -- a stock only gets credit for
        # strength if ALL THREE windows support it.
        z_combined = min(z3, z6, z12)
        is_strong = (z12 > MIN_STRENGTH_Z) and (z6 > MIN_STRENGTH_Z) and (z3 > MIN_STRENGTH_Z)

        shape = term_structure_shape(z12, z6, z3)

        combined[t] = {
            "m12_pct": v["m12"] * 100, "m6_pct": v["m6"] * 100, "m3_pct": v["m3"] * 100,
            "z12": z12, "z6": z6, "z3": z3, "z_combined": z_combined,
            "z_combined_sector": sector_z_map.get(t), "z_combined_cap": cap_z_map.get(t),
            "shape": shape, "shape_rank": SHAPE_RANK.get(shape, 2), "is_strong": is_strong,
            "recommendation": recommendation(z_combined, is_strong, SHAPE_RANK.get(shape, 2)),
        }

    # 1-10 score: percentile rank of z_combined within the index, no clipping.
    sorted_tickers = sorted(combined, key=lambda t: combined[t]["z_combined"])
    n = len(sorted_tickers)
    for i, t in enumerate(sorted_tickers):
        pct = i / (n - 1) if n > 1 else 0.5
        combined[t]["score_1_10"] = 1 + pct * 9

    stats = {"med12": med12, "mad12": mad12, "med6": med6, "mad6": mad6, "med3": med3, "mad3": mad3}
    return combined, stats

# ── Step 5: Export ───────────────────────────────────────────────────────────────
def export_csv(results, index_name, short_code, sectors=None, excluded=None, regime=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"momentum_{short_code}.csv")
    # Primary sort: z_combined (the actual strength signal). Secondary
    # tiebreaker: shape_rank (ACCELERATING/DIP ranked above HUMP/DECELERATING)
    # -- only matters when two stocks are essentially tied on z_combined.
    rows = sorted(results.items(), key=lambda kv: (kv[1]["z_combined"], kv[1]["shape_rank"]), reverse=True)
    sectors = sectors or {}

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"# Multi-Window (3-1, 6-1, 12-1) Momentum Strength — {index_name}"])
        w.writerow(["# z3/z6/z12 = vs index median/MAD for each window"])
        w.writerow(["# z_combined = min(z3, z6, z12) -- 'as strong as its weakest window' (PRIMARY)"])
        w.writerow(["# shape = ACCELERATING/DECELERATING (monotonic), HUMP (peaked at 6mo), DIP (troughed at 6mo), FLAT, or MIXED"])
        w.writerow(["# shape_rank (0-4) = ACCELERATING(4) > DIP(3) > FLAT/MIXED(2) > HUMP(1) > DECELERATING(0)"])
        w.writerow(["#   -- secondary tiebreaker only, ranking is primarily by z_combined"])
        w.writerow(["# is_strong = all three windows above threshold (z > %.2f)" % MIN_STRENGTH_Z])
        w.writerow(["# score_1_10 = percentile rank of z_combined within this index (SECONDARY, readability only)"])
        w.writerow(["# recommendation = STRONG BUY / BUY / HOLD / DON'T BUY (buy-side only, no sell ratings)"])
        w.writerow(["# z_combined_sector = same as z_combined but vs. peers in the same GICS sector only"])
        w.writerow([f"#   (blank if sector unknown or sector has < {SECTOR_MIN_GROUP} members -- too few to standardize)"])
        w.writerow(["# z_combined_cap = same as z_combined but vs. peers in the same cap-size tier only"])
        w.writerow([f"#   (blank if cap tier has < {SECTOR_MIN_GROUP} members -- too few to standardize)"])
        if regime is not None:
            state = "RISK-ON" if regime["risk_on"] else "RISK-OFF"
            w.writerow([f"# market_regime = {state} (SPY {regime['spy']:.2f} vs 200d MA "
                        f"{regime['ma200']:.2f}, as of {regime['date']})"])
            if not regime["risk_on"]:
                w.writerow(["# HOLD* = capped from BUY/STRONG BUY because market is below its 200-day MA"])
        else:
            w.writerow(["# market_regime = UNAVAILABLE (SPY fetch failed; buy signals not capped)"])
        if excluded:
            w.writerow([f"# Excluded ({len(excluded)}): " + ", ".join(excluded)])
        w.writerow([])
        w.writerow(["rank", "ticker", "sector", "momentum_12_1_pct", "momentum_6_1_pct", "momentum_3_1_pct",
                     "z_12_1", "z_6_1", "z_3_1", "z_combined", "z_combined_sector", "z_combined_cap", "shape", "shape_rank",
                     "is_strong", "score_1_10", "recommendation",
                     "pe_ratio", "pe_z", "pb_ratio", "pb_z", "score_3f", "rec_3f"])
        for i, (t, v) in enumerate(rows, 1):
            zsec    = round(v["z_combined_sector"], 4) if v["z_combined_sector"] is not None else ""
            zcap    = round(v["z_combined_cap"], 4) if v.get("z_combined_cap") is not None else ""
            pe_val  = round(v["pe_ratio"], 2) if v.get("pe_ratio") else ""
            pe_z_v  = round(v["pe_z"], 4) if v.get("pe_z") is not None else ""
            pb_val  = round(v["pb_ratio"], 2) if v.get("pb_ratio") else ""
            pb_z_v  = round(v["pb_z"], 4) if v.get("pb_z") is not None else ""
            s3_val  = round(v["score_3f"], 2) if v.get("score_3f") is not None else ""
            r3_val  = v.get("rec_3f") or ""
            w.writerow([i, t, sectors.get(t, "Unknown"), round(v["m12_pct"], 2), round(v["m6_pct"], 2), round(v["m3_pct"], 2),
                         round(v["z12"], 4), round(v["z6"], 4), round(v["z3"], 4), round(v["z_combined"], 4), zsec, zcap,
                         v["shape"], v["shape_rank"], v["is_strong"], round(v["score_1_10"], 2), v["recommendation"],
                         pe_val, pe_z_v, pb_val, pb_z_v, s3_val, r3_val])

    print(f"\n  Full ranked list saved: {path}")
    return rows

# ── Value signals: P/E and P/B ────────────────────────────────────────────────

def _fetch_financials(ticker, retries=3):
    """Annual filings for ticker, shared cache with backtest_value.py (90-day TTL)."""
    os.makedirs(FIN_CACHE_DIR, exist_ok=True)
    path = os.path.join(FIN_CACHE_DIR, f"{ticker}.json")
    if os.path.exists(path):
        age_days = (date.today() - date.fromtimestamp(os.path.getmtime(path))).days
        if age_days <= FIN_CACHE_MAX_AGE_DAYS:
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
    url = (f"{BASE_URL}/vX/reference/financials"
           f"?ticker={ticker}&timeframe=annual&limit=6&apiKey={API_KEY}")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            filings = data.get("results", [])
            with open(path, "w") as f:
                json.dump(filings, f)
            return filings
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
            else:
                break
        except Exception:
            break
    with open(path, "w") as f:
        json.dump([], f)
    return []


def _latest_filing(filings):
    """Most recent annual filing by filing_date (no as-of date cap — live use)."""
    valid = [f for f in filings if f.get("filing_date")]
    return max(valid, key=lambda f: f["filing_date"]) if valid else None


def _pe_ratio(filings, price):
    """P/E = price / diluted_EPS. Returns None if EPS <= 0, negative, or missing."""
    if not price or not filings:
        return None
    best = _latest_filing(filings)
    if not best:
        return None
    inc = best.get("financials", {}).get("income_statement", {})
    eps_entry = inc.get("diluted_earnings_per_share", {})
    eps = eps_entry.get("value") if isinstance(eps_entry, dict) else None
    if not eps or eps <= 0:
        return None
    return price / eps


def _pb_ratio(filings, price):
    """P/B = price / book-value-per-share. Returns None if equity <= 0 or missing."""
    if not price or not filings:
        return None
    best = _latest_filing(filings)
    if not best:
        return None
    fin = best.get("financials", {})
    eq_entry = fin.get("balance_sheet", {}).get("equity", {})
    eq = eq_entry.get("value") if isinstance(eq_entry, dict) else None
    sh_entry = fin.get("income_statement", {}).get("basic_average_shares", {})
    shares = sh_entry.get("value") if isinstance(sh_entry, dict) else None
    if not eq or eq <= 0 or not shares or shares <= 0:
        return None
    bps = eq / shares
    if bps <= 0:
        return None
    return price / bps


def _sector_value_z(raw_dict, sectors):
    """Sector-relative negated z-score: low ratio -> high (positive) score.
    Mirrors backtest_value.sector_relative_value logic for live-screener use."""
    from collections import defaultdict
    by_sec = defaultdict(list)
    for t, val in raw_dict.items():
        sec = sectors.get(t, "Unknown")
        if sec != "Unknown":
            by_sec[sec].append((t, val))
    out = {}
    for sec, members in by_sec.items():
        if len(members) < SECTOR_MIN_GROUP:
            continue
        vals = [v for _, v in members]
        med, mad = _med_mad(vals)
        if mad == 0:
            continue
        for t, v in members:
            out[t] = -((v - med) / mad)
    return out


def _pct_rank_dict(score_dict, universe):
    """Linear percentile rank 0→1 within universe, ascending (higher score = higher rank)."""
    srt = sorted(universe, key=lambda t: score_dict[t])
    n = len(srt)
    return {t: i / (n - 1) if n > 1 else 0.5 for i, t in enumerate(srt)}


def _rec_3f(score_3f, is_strong):
    """3-factor recommendation: requires both a strong 3F rank AND momentum confirmation."""
    if score_3f is None:
        return None
    if is_strong and score_3f >= 8.0:
        return "STRONG BUY"
    if is_strong and score_3f >= 6.0:
        return "BUY"
    if score_3f >= 5.0:
        return "HOLD"
    return "DON'T BUY"


def add_value_scores(tickers, snap, sectors, results):
    """
    Fetch P/E and P/B for every ticker, compute sector-relative z-scores, and
    attach pe_ratio / pb_ratio / pe_z / pb_z / score_3f / rec_3f to each entry
    in results in-place.
    Shares .cache/financials/ with backtest_value.py — near-instant if pre-warmed.
    score_3f is average percentile rank of (SecZ, pe_z, pb_z), scaled 1-10.
    """
    prices = snap["p21"]

    print(f"\n{'─'*60}")
    print("STEP 4 — Fetching fundamentals (P/E + P/B) for value signals")
    print(f"{'─'*60}")
    print(f"  (First run: ~30s if cache empty. Subsequent runs: near-instant.)")

    def _fetch_one(t):
        cache_path = os.path.join(FIN_CACHE_DIR, f"{t}.json")
        cache_hit = (
            os.path.exists(cache_path) and
            (date.today() - date.fromtimestamp(os.path.getmtime(cache_path))).days <= FIN_CACHE_MAX_AGE_DAYS
        )
        data = _fetch_financials(t)
        if not cache_hit:
            time.sleep(0.05)  # rate-limit only actual API calls
        return t, data

    filings = {}
    n_total = len(tickers)
    done_count = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            t, data = future.result()
            filings[t] = data
            done_count += 1
            if done_count % 100 == 0:
                print(f"  {done_count}/{n_total} financials loaded")

    pe_raw = {t: v for t in tickers
              if (v := _pe_ratio(filings.get(t, []), prices.get(t))) is not None}
    pe_z = _sector_value_z(pe_raw, sectors)

    pb_raw = {t: v for t in tickers
              if (v := _pb_ratio(filings.get(t, []), prices.get(t))) is not None}
    pb_z = _sector_value_z(pb_raw, sectors)

    print(f"  P/E data: {len(pe_z)} tickers  |  P/B data: {len(pb_z)} tickers")

    # 3-factor percentile rank: SecZ + sector-relative P/E-z + sector-relative P/B-z
    secz_valid = {t: v["z_combined_sector"]
                  for t, v in results.items() if v.get("z_combined_sector") is not None}
    common3 = set(secz_valid) & set(pe_z) & set(pb_z)

    if len(common3) >= SECTOR_MIN_GROUP:
        mp = _pct_rank_dict(secz_valid, common3)
        pp = _pct_rank_dict(pe_z, common3)
        bp = _pct_rank_dict(pb_z, common3)
        comb3 = {t: (mp[t] + pp[t] + bp[t]) / 3 for t in common3}
    else:
        comb3 = {}

    print(f"  3-factor coverage: {len(comb3)}/{len(tickers)} tickers")

    for t, v in results.items():
        v["pe_ratio"] = pe_raw.get(t)
        v["pb_ratio"] = pb_raw.get(t)
        v["pe_z"]     = pe_z.get(t)
        v["pb_z"]     = pb_z.get(t)
        s3 = round(1 + comb3[t] * 9, 2) if t in comb3 else None
        v["score_3f"] = s3
        v["rec_3f"]   = _rec_3f(s3, v.get("is_strong", False))


# ── Main ──────────────────────────────────────────────────────────────────────
def _extra_value_scores(ticker, v, sector, results, sectors):
    """Sector-relative value z-scores + 3F score for a ticker scored against
    a universe it isn't part of. Mirrors _score_extra_ticker's rank-insertion
    approach: builds the sector's median/MAD and the 3-factor percentile
    ranks from the universe's results, then slots this ticker in without
    folding it into the distribution."""
    members = [t for t, s in sectors.items()
               if s == sector and t != ticker and t in results]

    def _value_z(key):
        raw = v.get(key)
        if raw is None:
            return None
        vals = [results[t][key] for t in members if results[t].get(key) is not None]
        if len(vals) < SECTOR_MIN_GROUP:
            return None
        med, mad = _med_mad(vals)
        if mad == 0:
            return None
        return -((raw - med) / mad)

    v["pe_z"] = _value_z("pe_ratio")
    v["pb_z"] = _value_z("pb_ratio")

    secz = v.get("z_combined_sector")
    if secz is None or v["pe_z"] is None or v["pb_z"] is None:
        v["score_3f"] = v["rec_3f"] = None
        return
    common3 = [t for t in results
               if results[t].get("z_combined_sector") is not None
               and results[t].get("pe_z") is not None
               and results[t].get("pb_z") is not None]
    if len(common3) < SECTOR_MIN_GROUP:
        v["score_3f"] = v["rec_3f"] = None
        return

    def _pct(key, val):
        vals = [results[t][key] for t in common3]
        return sum(1 for x in vals if x <= val) / len(vals)

    comb = (_pct("z_combined_sector", secz)
            + _pct("pe_z", v["pe_z"])
            + _pct("pb_z", v["pb_z"])) / 3
    v["score_3f"] = round(1 + comb * 9, 2)
    v["rec_3f"] = _rec_3f(v["score_3f"], v.get("is_strong", False))


def run(index_key):
    tickers, index_name, sectors = get_constituents(index_key)
    short_code = INDEXES[index_key][2]

    snap = get_market_snapshots()

    print(f"\n{'─'*60}")
    print(f"STEP 3 — Computing multi-window momentum for {len(tickers)} constituents")
    print(f"{'─'*60}")
    momentum_dict, excluded = compute_momentum(tickers, snap)
    print(f"  {len(momentum_dict)} stocks with valid data")

    fill_unknown_sectors(tickers, sectors)
    results, stats = standardize(momentum_dict, sectors, build_cap_tiers())
    n_strong = sum(1 for v in results.values() if v["is_strong"])
    print(f"  Index 12-1 median: {stats['med12']*100:+.2f}%  MAD: {stats['mad12']*100:.2f}%")
    print(f"  Index 6-1  median: {stats['med6']*100:+.2f}%  MAD: {stats['mad6']*100:.2f}%")
    print(f"  Index 3-1  median: {stats['med3']*100:+.2f}%  MAD: {stats['mad3']*100:.2f}%")
    print(f"  {n_strong} of {len(results)} stocks classified 'strong' (all 3 windows z > {MIN_STRENGTH_Z})")

    add_value_scores(tickers, snap, sectors, results)

    regime = get_market_regime()
    print_regime(regime)
    apply_regime_cap(results, regime)

    rows = export_csv(results, index_name, short_code, sectors, excluded, regime)

    n = min(TOP_N, len(rows))
    print(f"\n{'─'*60}")
    print(f"TOP {n} — {index_name}  (by momentum z-score)")
    print(f"  12-1: {snap['d252']} → {snap['d21']}   6-1: {snap['d126']} → {snap['d21']}   3-1: {snap['d63']} → {snap['d21']}")
    print(f"{'─'*60}")
    print(f"  {'Rank':<5} {'Ticker':<8} {'Sector':<26} {'12-1%':>8} {'6-1%':>8} {'3-1%':>8} {'Z12':>6} {'Z6':>6} {'Z3':>6} {'Zcomb':>6} {'SecZ':>6} {'CapZ':>6} {'Shape':<13} {'1-10':>6} {'Call':<11}")
    for i, (t, v) in enumerate(rows[:n], 1):
        sec = sectors.get(t, "Unknown")
        secz = f"{v['z_combined_sector']:>6.2f}" if v["z_combined_sector"] is not None else f"{'--':>6}"
        capz = f"{v['z_combined_cap']:>6.2f}" if v.get("z_combined_cap") is not None else f"{'--':>6}"
        print(f"  {i:<5} {t:<8} {sec:<26} {v['m12_pct']:>7.2f}% {v['m6_pct']:>7.2f}% {v['m3_pct']:>7.2f}% "
              f"{v['z12']:>6.2f} {v['z6']:>6.2f} {v['z3']:>6.2f} {v['z_combined']:>6.2f} {secz} {capz} "
              f"{v['shape']:<13} {v['score_1_10']:>6.2f} {v['recommendation']:<11}")

    # Sector concentration check on the top N -- "Unknown" is excluded since
    # it's a data gap (no GICS info scraped), not an actual sector pile-up.
    real_sectors = [sectors.get(t, "Unknown") for t, _ in rows[:n] if sectors.get(t, "Unknown") != "Unknown"]
    if real_sectors:
        from collections import Counter
        top_sectors = Counter(real_sectors)
        most_common, count = top_sectors.most_common(1)[0]
        if count >= n / 2:
            print(f"\n  ⚠ CONCENTRATION: {count}/{n} of the top stocks are '{most_common}' — "
                  f"this top-{n} is mostly one sector, not a diversified picture of relative strength.")
    n_secz = sum(1 for v in results.values() if v["z_combined_sector"] is not None)
    n_capz = sum(1 for v in results.values() if v.get("z_combined_cap") is not None)
    print(f"\n  Coverage: SecZ available for {n_secz}/{len(results)} stocks, "
          f"CapZ for {n_capz}/{len(results)} (rest show '--' -- group too small/unknown to standardize).")
    print(f"  SecZ = sector-relative z_combined (vs peers in same GICS sector, '--' = sector unknown or "
          f"too small to standardize). Use it to compare stocks ACROSS sectors fairly.")
    print(f"  CapZ = cap-tier-relative z_combined (vs peers in the same crude size tier -- "
          f"Large/Mid/Small by index membership, '--' = tier too small to standardize).")
    if index_key in SINGLE_SECTOR_INDEXES:
        print(f"  Note: {index_name} is single-sector by definition -- SecZ here is "
              f"nearly identical to Zcomb and not a meaningful diversification check.")
    print(f"  Recommendation = a label derived purely from this script's z-score/shape math, "
          f"NOT a backtested or validated trading signal -- treat as a relative-strength summary.")
    if regime is not None and not regime["risk_on"]:
        print(REGIME_CAP_NOTE)

    # ── 3-Factor top 10 ──────────────────────────────────────────────────────
    rows_3f = sorted(
        [(t, v) for t, v in results.items() if v.get("score_3f") is not None],
        key=lambda kv: kv[1]["score_3f"], reverse=True
    )
    n3 = min(TOP_N, len(rows_3f))
    if n3 > 0:
        print(f"\n{'─'*60}")
        print(f"3-FACTOR TOP {n3} — {index_name}  (Momentum + P/E + P/B combined)")
        print(f"{'─'*60}")
        print(f"  {'Rank':<5} {'Ticker':<8} {'Sector':<26} {'P/E':>7} {'P/B':>6} {'SecZ':>7} {'3F':>6} {'3F-Call':<11}")
        for i, (t, v) in enumerate(rows_3f[:n3], 1):
            pe   = f"{v['pe_ratio']:>7.1f}" if v.get("pe_ratio") else f"{'--':>7}"
            pb   = f"{v['pb_ratio']:>6.2f}" if v.get("pb_ratio") else f"{'--':>6}"
            secz = f"{v['z_combined_sector']:>7.2f}" if v.get("z_combined_sector") is not None else f"{'--':>7}"
            print(f"  {i:<5} {t:<8} {sectors.get(t,'Unknown'):<26} {pe} {pb} {secz} {v['score_3f']:>6.2f} {v['rec_3f'] or '--':<11}")
    else:
        print(f"\n  3-Factor Score: insufficient P/E/P/B data for this index.")

    print(f"\n✓ Done. (Single-window momentum >=100% is excluded as suspect data.)")


def _score_extra_ticker(ticker, m, momentum_dict, results, stats, group_of, cap_tiers):
    """Build the result dict for a ticker that's NOT part of `momentum_dict`
    (i.e. not a constituent of the universe being scored against). Shared by
    run_single() and run_vs_diverse() -- same math, different group_of map.
    """
    z12 = robust_z(None, m["m12"], stats["med12"], stats["mad12"])
    z6  = robust_z(None, m["m6"],  stats["med6"],  stats["mad6"])
    z3  = robust_z(None, m["m3"],  stats["med3"],  stats["mad3"])
    z_combined = min(z3, z6, z12)
    is_strong = (z12 > MIN_STRENGTH_Z) and (z6 > MIN_STRENGTH_Z) and (z3 > MIN_STRENGTH_Z)
    shape = term_structure_shape(z12, z6, z3)
    shape_rank = SHAPE_RANK.get(shape, 2)
    all_z = sorted(r["z_combined"] for r in results.values())
    n = len(all_z)
    rank = sum(1 for x in all_z if x <= z_combined)
    score = 1 + (rank / n if n else 0.5) * 9
    cap_override = classify_cap_tier(get_market_cap(ticker)) or cap_tiers.get(ticker)
    return {
        "m12_pct": m["m12"]*100, "m6_pct": m["m6"]*100, "m3_pct": m["m3"]*100,
        "z12": z12, "z6": z6, "z3": z3, "z_combined": z_combined,
        "shape": shape, "shape_rank": shape_rank,
        "is_strong": is_strong, "score_1_10": score,
        "z_combined_sector": _group_z_for_ticker(ticker, m, momentum_dict, group_of),
        "z_combined_cap": _group_z_for_ticker(ticker, m, momentum_dict, cap_tiers, group_override=cap_override),
        "recommendation": recommendation(z_combined, is_strong, shape_rank),
    }


def _print_single_result(ticker, v, sectors, cap_tiers, header):
    print(f"\n{'─'*60}")
    print(header)
    print(f"{'─'*60}")
    print(f"  Sector        : {sectors.get(ticker, 'Unknown')}")
    mcap = get_market_cap(ticker)
    tier = classify_cap_tier(mcap) or cap_tiers.get(ticker, "?")
    if mcap:
        print(f"  Market cap    : ${mcap/1e9:,.1f}B  -> {tier} (real market cap)")
    else:
        print(f"  Market cap    : unavailable -> {tier} (index-membership proxy)")
    print(f"  12-1 momentum : {v['m12_pct']:+7.2f}%  (z = {v['z12']:+.3f})")
    print(f"  6-1  momentum : {v['m6_pct']:+7.2f}%  (z = {v['z6']:+.3f})")
    print(f"  3-1  momentum : {v['m3_pct']:+7.2f}%  (z = {v['z3']:+.3f})")
    print(f"  Combined z    : {v['z_combined']:+.3f}")
    secz = f"{v['z_combined_sector']:+.3f}" if v.get('z_combined_sector') is not None else "--"
    capz = f"{v['z_combined_cap']:+.3f}" if v.get('z_combined_cap') is not None else "--"
    print(f"  Sector-rel z  : {secz}")
    print(f"  CapTier-rel z : {capz}")
    print(f"  Shape         : {v['shape']}  (shape_rank {v['shape_rank']}/4 -- secondary tiebreaker only)")
    print(f"  Strong?       : {v['is_strong']}")
    print(f"  Score 1-10    : {v['score_1_10']:.2f}  (momentum only)")
    print(f"  {'─'*50}")
    pe_s   = f"{v['pe_ratio']:.1f}" if v.get("pe_ratio") else "--"
    pe_z_s = f"{v['pe_z']:+.3f}" if v.get("pe_z") is not None else "--"
    pb_s   = f"{v['pb_ratio']:.2f}" if v.get("pb_ratio") else "--"
    pb_z_s = f"{v['pb_z']:+.3f}" if v.get("pb_z") is not None else "--"
    print(f"  P/E ratio     : {pe_s}  (sector-rel z = {pe_z_s})")
    print(f"  P/B ratio     : {pb_s}  (sector-rel z = {pb_z_s})")
    if v.get("score_3f") is not None:
        print(f"  3-Factor Score: {v['score_3f']:.2f}/10  →  {v['rec_3f']}")
    else:
        has_pe = v.get("pe_ratio") is not None
        has_pb = v.get("pb_ratio") is not None
        if not has_pe and not has_pb:
            missing = "No P/E & P/B"
        elif not has_pe:
            missing = "No P/E"
        elif not has_pb:
            missing = "No P/B"
        else:
            missing = "No Sector Data"
        print(f"  3-Factor Score: --  ({missing} — Invalid)")


def run_single(ticker, index_key):
    """Score one ticker against an index's distribution.

    The index baseline (median/MAD for both windows) is computed from the
    index's OWN constituents only. If the target ticker isn't a constituent,
    it's scored against that baseline without being folded into it.
    """
    ticker = ticker.upper().strip()
    tickers, index_name, sectors = get_constituents(index_key)
    ticker_in_index = ticker in tickers

    snap = get_market_snapshots()
    momentum_dict, _ = compute_momentum(tickers, snap)
    cap_tiers = build_cap_tiers()
    fill_unknown_sectors(tickers, sectors)
    results, stats = standardize(momentum_dict, sectors, cap_tiers)
    add_value_scores(tickers, snap, sectors, results)

    if ticker_in_index:
        if ticker not in results:
            print(f"\n  {ticker}: no valid data (missing price or suspect move).")
            return
        v = results[ticker]
    else:
        extra, _ = compute_momentum([ticker], snap)
        if ticker not in extra:
            print(f"\n  {ticker}: no valid data (missing price or suspect move).")
            return
        sec = sectors.get(ticker, "Unknown")
        if sec == "Unknown":
            sec = get_sector_api(ticker) or "Unknown"
        sectors = {**sectors, ticker: sec}
        v = _score_extra_ticker(ticker, extra[ticker], momentum_dict, results, stats, sectors, cap_tiers)
        extra_filings = _fetch_financials(ticker)
        extra_price = snap["p21"].get(ticker)
        v["pe_ratio"] = _pe_ratio(extra_filings, extra_price)
        v["pb_ratio"] = _pb_ratio(extra_filings, extra_price)
        _extra_value_scores(ticker, v, sec, results, sectors)
        print(f"\n  Note: {ticker} is not a constituent of {index_name} -- "
              f"scored against the index's distribution, not included in it.")

    regime = get_market_regime()
    print_regime(regime)
    apply_regime_cap({ticker: v}, regime)
    _print_single_result(ticker, v, sectors, cap_tiers, f"{ticker} — Multi-Window Momentum vs {index_name}")
    if regime is not None and not regime["risk_on"] and (
            v.get("recommendation") == "HOLD*" or v.get("rec_3f") == "HOLD*"):
        print(REGIME_CAP_NOTE)


def run_vs_diverse(ticker):
    """Score one ticker against a fixed, sector-diverse ~110-stock universe
    (DIVERSE_UNIVERSE) instead of an index. Useful for tickers that aren't
    in the S&P 500 or Dow, or when you just want "vs. broad market" rather
    than "vs. this specific index."
    """
    ticker = ticker.upper().strip()
    universe = list(DIVERSE_UNIVERSE.keys())
    in_universe = ticker in DIVERSE_UNIVERSE

    snap = get_market_snapshots()

    print(f"\n{'─'*60}")
    print(f"Computing momentum for diverse {len(universe)}-stock universe")
    print(f"{'─'*60}")
    momentum_dict, excluded = compute_momentum(universe, snap)
    print(f"  {len(momentum_dict)} stocks with valid data")
    cap_tiers = build_cap_tiers()
    results, stats = standardize(momentum_dict, DIVERSE_UNIVERSE, cap_tiers)
    add_value_scores(universe, snap, DIVERSE_UNIVERSE, results)

    sectors_x = dict(DIVERSE_UNIVERSE)
    if in_universe:
        if ticker not in results:
            print(f"\n  {ticker}: no valid data (missing price or suspect move).")
            return
        v = results[ticker]
    else:
        extra, _ = compute_momentum([ticker], snap)
        if ticker not in extra:
            print(f"\n  {ticker}: no valid data (missing price or suspect move).")
            return
        sec = get_sector_api(ticker) or "Unknown"
        sectors_x[ticker] = sec
        v = _score_extra_ticker(ticker, extra[ticker], momentum_dict, results, stats, sectors_x, cap_tiers)
        extra_filings = _fetch_financials(ticker)
        extra_price = snap["p21"].get(ticker)
        v["pe_ratio"] = _pe_ratio(extra_filings, extra_price)
        v["pb_ratio"] = _pb_ratio(extra_filings, extra_price)
        _extra_value_scores(ticker, v, sec, results, sectors_x)
        print(f"\n  Note: {ticker} is not part of the diverse universe -- "
              f"scored against it, not included in it.")

    regime = get_market_regime()
    print_regime(regime)
    apply_regime_cap({ticker: v}, regime)
    _print_single_result(ticker, v, sectors_x, cap_tiers,
                          f"{ticker} — Multi-Window Momentum vs Diverse {len(universe)}-Stock Universe")
    if regime is not None and not regime["risk_on"] and (
            v.get("recommendation") == "HOLD*" or v.get("rec_3f") == "HOLD*"):
        print(REGIME_CAP_NOTE)



def scan_best(top_n=10):
    """Scan a broad cross-index, all-sector universe (S&P 500 + Nasdaq-100 +
    S&P MidCap 400 + the diverse 110) and surface the stocks with BOTH the
    best score_1_10 AND a "momentum building/recovering" shape
    (ACCELERATING or DIP).

    [Important honesty note, printed to the user too]: this does NOT
    guarantee the single "best" stock in the market -- it's the best
    among ~700-900 large/mid-cap US names this script can see. Filtering
    to ACCELERATING/DIP only is also a judgment call (see shape_rank docs);
    a DECELERATING stock with a much higher z_combined could still be a
    better holding. Use this as a shortlist generator, not a verdict.
    """
    print(f"\n{'─'*60}")
    print("STEP 1 — Building combined scan universe")
    print(f"{'─'*60}")

    # cap_tiers: static, free classification by which index a ticker comes
    # from -- S&P 500 = large, Nasdaq-100 = large (overlaps S&P 500 heavily,
    # fine either way), S&P MidCap 400 = mid, diverse-only extras = large
    # (all DIVERSE_UNIVERSE names are large-caps). This is a crude proxy,
    # NOT actual market cap -- see run_vs_diverse/scan_best docstrings.
    cap_tiers = build_cap_tiers()

    all_tickers, all_sectors = {}, {}
    for key in ("A", "C", "D"):  # S&P 500, Nasdaq-100, S&P MidCap 400
        try:
            tks, nm, secs = get_constituents(key)
        except Exception as e:
            print(f"  ⚠ Skipping {INDEXES[key][0]} ({e})")
            continue
        for t in tks:
            all_tickers[t] = True
            all_sectors.setdefault(t, secs.get(t, "Unknown"))
    for t, sec in DIVERSE_UNIVERSE.items():
        all_tickers.setdefault(t, True)
        all_sectors.setdefault(t, sec)

    universe = sorted(all_tickers.keys())
    print(f"  Combined universe: {len(universe)} unique tickers across all sectors")

    snap = get_market_snapshots()

    print(f"\n{'─'*60}")
    print(f"STEP 2 — Computing momentum for {len(universe)} tickers")
    print(f"{'─'*60}")
    momentum_dict, excluded = compute_momentum(universe, snap)
    print(f"  {len(momentum_dict)} stocks with valid data")

    fill_unknown_sectors(universe, all_sectors)
    results, stats = standardize(momentum_dict, all_sectors, cap_tiers)
    add_value_scores(universe, snap, all_sectors, results)

    regime = get_market_regime()
    print_regime(regime)
    apply_regime_cap(results, regime)

    # "Best" = momentum currently building/recovering (ACCELERATING or DIP)
    # AND ranked by z_combined within that subset.
    candidates = [(t, v) for t, v in results.items() if v["shape"] in ("ACCELERATING", "DIP")]
    candidates.sort(key=lambda kv: (kv[1]["z_combined"], kv[1]["shape_rank"]), reverse=True)

    n = min(top_n, len(candidates))
    print(f"\n{'─'*60}")
    print(f"BEST {n} — ACCELERATING/DIP stocks, ranked by combined z-score")
    print(f"  (out of {len(results)} scored, {len(candidates)} have an ACCELERATING/DIP shape)")
    print(f"{'─'*60}")
    print(f"  {'Rank':<5} {'Ticker':<8} {'Sector':<26} {'Cap':<6} {'Zcomb':>6} {'3F':>6} {'Shape':<13} {'Call':<22}")
    for i, (t, v) in enumerate(candidates[:n], 1):
        sec = all_sectors.get(t, "Unknown")
        cap = cap_tiers.get(t, "?")
        s3  = f"{v['score_3f']:>6.2f}" if v.get("score_3f") is not None else f"{'--':>6}"
        if v.get("rec_3f"):
            call = v["rec_3f"]
        else:
            has_pe = v.get("pe_ratio") is not None
            has_pb = v.get("pb_ratio") is not None
            if not has_pe and not has_pb:
                missing = "No P/E & P/B"
            elif not has_pe:
                missing = "No P/E"
            elif not has_pb:
                missing = "No P/B"
            else:
                missing = "No Sector Data"
            call = f"{missing} — Invalid"
        print(f"  {i:<5} {t:<8} {sec:<26} {cap:<6} {v['z_combined']:>6.2f} {s3} {v['shape']:<13} {call:<22}")
    if candidates:
        print(f"\n  Cap = crude size tier by index membership (Large = S&P500/Nasdaq100, Mid = S&P MidCap 400).")
        print(f"  3F = 3-factor score (Momentum + P/E + P/B average percentile rank, 1-10 scale).")
        print(f"  Call shows reason when 3F unavailable: NO P/E = negative/zero earnings; NO P/B = negative equity; NO SECTOR DATA = sector too small.")
        if regime is not None and not regime["risk_on"]:
            print(REGIME_CAP_NOTE)

    if not candidates:
        print("  None found -- no stock in this universe is currently ACCELERATING or DIP.")
        return


def _run_index_menu(index_key):
    print(f"\n  1. Top 10 ranked stocks in this index")
    print(f"  2. Score a single stock against this index")
    mode = input("Choose (1 or 2): ").strip()
    if mode == "2":
        sym = input("Enter ticker symbol: ").strip()
        run_single(sym, index_key)
    else:
        run(index_key)


if __name__ == "__main__":
    print("─" * 60)
    print("MULTI-WINDOW MOMENTUM STRENGTH SCREENER")
    print("─" * 60)
    print("  1. S&P 500")
    print("  2. Dow Jones Industrial Average")
    print("  3. Nasdaq-100")
    print("  4. S&P MidCap 400")
    print("  5. S&P SmallCap 600")
    print("  6. Score any ticker vs a diverse 110-stock universe (all sectors)")
    print("  7. Scan ALL sectors/indexes for the best ACCELERATING/DIP stocks")
    print("  8. Custom index (choose from 8 indexes)")
    choice = input("\nChoose (1-8): ").strip()

    if choice in TOP_MENU_INDEXES:
        _run_index_menu(TOP_MENU_INDEXES[choice])
    elif choice == "6":
        sym = input("Enter ticker symbol: ").strip()
        run_vs_diverse(sym)
    elif choice == "7":
        scan_best()
    elif choice == "8":
        keys = CUSTOM_INDEX_KEYS
        print()
        for i, k in enumerate(keys, 1):
            print(f"  {i}. {INDEXES[k][0]}")
        sel = input(f"\nChoose (1-{len(keys)}): ").strip()
        try:
            idx_key = keys[int(sel) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            idx_key = None
        if idx_key:
            _run_index_menu(idx_key)
    else:
        print("Invalid choice.")
