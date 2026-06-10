"""
liquidity_analysis.py
======================
Liquidity factor for the FlexTrade equity-research dashboard.

Given a stock ticker and a date range, this module scores a company's
liquidity and returns a signal on a four-band scale:

    STRONG  /  ADEQUATE  /  WATCH  /  WEAK

It is meant to be imported by the shared dashboard alongside the other
interns' factor modules:

    from liquidity_analysis import liquidity_analysis
    result = liquidity_analysis("AAPL", "2021-01-01", "2023-12-31")

"""

import os
import json

import requests


# ======================================================================
# EDIT THIS WHEN THE API KEY CHANGES  (kept as a header so it's easy to swap)
# ======================================================================
MASSIVE_API_KEY = "YOUR_API_KEY_HERE"
# ======================================================================

BASE_URL = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}

# Endpoint paths for each statement we need.
CASH_FLOW_ENDPOINT = "/stocks/financials/v1/cash-flow-statements"
INCOME_ENDPOINT = "/stocks/financials/v1/income-statements"
BALANCE_SHEET_ENDPOINT = "/stocks/financials/v1/balance-sheets"

# Exact JSON field names in the Massive API response (one per metric we need).
FIELD_CFO = "net_cash_from_operating_activities"
FIELD_CAPEX = "purchase_of_property_plant_and_equipment"
FIELD_DIVIDENDS = "dividends"
FIELD_INTEREST = "interest_expense"
FIELD_CURRENT_LIABILITIES = "total_current_liabilities"


# ----------------------------------------------------------------------
# Band thresholds.  Each tuple is (watch_min, adequate_min, strong_min):
# anything below watch_min is WEAK, at/above strong_min is STRONG, etc.
# ----------------------------------------------------------------------
OCF_BANDS = (0.25, 0.50, 1.00)
FCF_BANDS = (0.50, 1.00, 1.50)
COMPOSITE_BANDS = (0.25, 0.50, 0.75)  # applied to the 0-1 composite score

# ----------------------------------------------------------------------
# Normalisation anchors.  Each is a list of (raw_ratio, normalised_score)
# control points.  We interpolate linearly between them and clamp to [0, 1].
# The anchors are placed so that the raw band boundaries land exactly on
# the normalised cut-points 0.25 / 0.50 / 0.75, with a sensible cap above
# which the score saturates at 1.0.
# ----------------------------------------------------------------------
OCF_ANCHORS = [(0.0, 0.0), (0.25, 0.25), (0.50, 0.50), (1.00, 0.75), (2.00, 1.0)]
FCF_ANCHORS = [(0.0, 0.0), (0.50, 0.25), (1.00, 0.50), (1.50, 0.75), (2.50, 1.0)]

# The composite weighting from the spec.
OCF_WEIGHT = 0.4
FCF_WEIGHT = 0.6


# ======================================================================
# Small helper functions
# ======================================================================
def _band(value, bands):
    """Map a numeric value onto STRONG / ADEQUATE / WATCH / WEAK.

    `bands` is (watch_min, adequate_min, strong_min) in ascending order.
    """
    watch_min, adequate_min, strong_min = bands
    if value >= strong_min:
        return "STRONG"
    if value >= adequate_min:
        return "ADEQUATE"
    if value >= watch_min:
        return "WATCH"
    return "WEAK"


def _normalise(value, anchors):
    """Piecewise-linear map from a raw ratio to a 0-1 score.

    Below the first anchor we clamp to its score; above the last anchor we
    clamp to its score; in between we interpolate linearly.
    """
    # Clamp at the ends.
    if value <= anchors[0][0]:
        return anchors[0][1]
    if value >= anchors[-1][0]:
        return anchors[-1][1]

    # Find the segment [x0, x1] that contains `value` and interpolate.
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= value <= x1:
            # How far along this segment we are, 0.0 .. 1.0
            fraction = (value - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)

    # Should never reach here, but be safe.
    return anchors[-1][1]


def _mean(numbers):
    """Plain average of a list of numbers (assumes the list is non-empty)."""
    return sum(numbers) / len(numbers)


def _fetch(endpoint, ticker, start_date, end_date):
    """Pull annual statements for one ticker within a date range.

    Returns the `results` list from the Massive response (possibly empty).
    Raises a clear error on an HTTP / authentication failure.
    """
    url = BASE_URL + endpoint
    params = {
        "tickers": ticker,
        "timeframe": "annual",
        "period_end.gte": start_date,
        "period_end.lte": end_date,
        "limit": 100,
        "sort": "period_end.asc",
    }

    response = requests.get(url, headers=HEADERS, params=params, timeout=30)

    # Turn HTTP errors (e.g. 401 bad key, 403 wrong tier) into a clear message.
    if not response.ok:
        raise RuntimeError(
            f"Massive API request to {endpoint} failed "
            f"(HTTP {response.status_code}). If this is 401/403, check that "
            f"MASSIVE_API_KEY is current and has fundamentals access. "
            f"Response: {response.text[:300]}"
        )

    data = response.json()
    return data.get("results", [])


def _index_by_fiscal_year(results):
    """Turn a list of statement records into {fiscal_year: record}."""
    indexed = {}
    for record in results:
        year = record.get("fiscal_year")
        if year is not None:
            indexed[year] = record
    return indexed


def _describe(ticker, signal, ocf_value, fcf_value, ocf_band, fcf_band,
              operational_score, financial_score):
    """Company-specific plain-English description of the liquidity signal.

    Always three sentences:
      1. "{TICKER} has a {SIGNAL} position with {OCF_BAND} operational and
         {FCF_BAND} financial performance."
      2. "The OCF ratio is X, and the FCF coverage ratio is Y."
      3. "Therefore, {conclusion}."

    WATCH / WEAK conclusions are case-specific (operational drag, financial
    drag, or both). NO_DATA returns a short fallback with no ratio lines.
    """
    # NO_DATA: no ratio values exist, skip the opening and ratio lines.
    if signal == "NO_DATA" or ocf_value is None or fcf_value is None:
        return (f"{ticker}: insufficient annual filings to assess liquidity "
                "for this ticker and date range.")

    opening = (f"{ticker} has a {signal} liquidity position with "
               f"{ocf_band} operational performance and "
               f"{fcf_band} financial performance.")

    ratios = (f"The OCF ratio is {ocf_value:.2f}, "
              f"and the FCF coverage ratio is {fcf_value:.2f}.")

    # --- Conclusion: band-specific logic ----------------------------------
    if signal == "STRONG":
        conclusion = ("operating cash flow comfortably covers near-term "
                      "liabilities and leaves ample free cash for all debt "
                      "and dividend obligations.")
    elif signal == "ADEQUATE":
        conclusion = ("the company generates enough cash to cover short-term "
                      "liabilities and obligations, but with only a modest buffer.")
    else:
        # WATCH / WEAK: identify which leg is the drag and explain it.
        # Treat both as jointly weak when scores are within this margin.
        op = operational_score if operational_score is not None else 0.0
        fin = financial_score if financial_score is not None else 0.0
        margin = 0.1

        if abs(op - fin) <= margin:
            driver = ("both legs are weak: operating cash flow is low relative "
                      "to current liabilities, and free cash flow gives little "
                      "cover for debt and dividend obligations")
        elif op < fin:
            driver = ("the operational leg is the main drag — operating cash "
                      "flow is low relative to current liabilities, so the "
                      "business is not generating enough cash to comfortably "
                      "cover what is due within the year")
        else:
            driver = ("the forward-looking leg is the main drag — after capital "
                      "spending, free cash flow gives limited cover for debt "
                      "interest and dividend obligations")

        if signal == "WATCH":
            conclusion = f"liquidity is thin with little margin for error, and {driver}."
        else:
            conclusion = f"cash is falling short of near-term obligations, and {driver}."

    return f"{opening} {ratios} Therefore, {conclusion}"


def _summarise(detail, save_detail, detail_dir):
    """Persist the full breakdown (optional) and return the compact result.

    `detail` is the complete internal record. We optionally write it to a JSON
    file so nothing is lost, then return ONLY the fields the dashboard needs:
    signal, a one-sentence description, composite score, and the two part-scores.
    """
    if save_detail:
        os.makedirs(detail_dir, exist_ok=True)
        period = detail["period"]
        filename = f"{detail['ticker']}_{period['start']}_to_{period['end']}_liquidity.json"
        with open(os.path.join(detail_dir, filename), "w") as handle:
            json.dump(detail, handle, indent=2)

    return {
        "signal": detail["signal"],                 # STRONG/ADEQUATE/WATCH/WEAK
        "description": _describe(
            detail["ticker"], detail["signal"],
            (detail["ocf_ratio"] or {}).get("value"),
            (detail["fcf_coverage"] or {}).get("value"),
            (detail["ocf_ratio"] or {}).get("band"),
            (detail["fcf_coverage"] or {}).get("band"),
            detail["operational_score"],
            detail["financial_score"],
        ),
        "composite_score": detail["liquidity_score"],
        "operational_score": detail["operational_score"],  # normalised OCF (the 0.4 part)
        "financial_score": detail["financial_score"],      # normalised FCF (the 0.6 part)
    }


# ======================================================================
# Main entry point
# ======================================================================
def liquidity_analysis(ticker, start_date, end_date, save_detail=True, detail_dir="."):
    """Score a stock's liquidity over an explicit date range.

    Parameters
    ----------
    ticker : str
        Stock ticker, e.g. "AAPL".
    start_date, end_date : str
        Inclusive date range in "YYYY-MM-DD" format. All annual filings whose
        `period_end` falls in this range are used and their ratios averaged.
    save_detail : bool, default True
        If True, the full breakdown (raw inputs per year, the per-statement
        figures used, and any warnings) is written to a JSON file so nothing is
        lost. Set False when calling in bulk to avoid writing lots of files.
    detail_dir : str, default "."
        Folder the detail JSON is written to (created if needed).

    Returns
    -------
    dict
        Compact result with ONLY:
          - "signal"            STRONG / ADEQUATE / WATCH / WEAK (or "NO_DATA")
          - "description"       one-sentence plain-English reading of the signal
          - "composite_score"   final 0-1 liquidity score
          - "operational_score" normalised OCF sub-score (the 0.4 weight part)
          - "financial_score"   normalised FCF sub-score (the 0.6 weight part)
        The full breakdown is saved to disk (see `save_detail`), not returned.
    """
    ticker = ticker.upper()
    warnings = []

    # --- 1. Pull the three statements and index each by fiscal year --------
    cash_flow = _index_by_fiscal_year(_fetch(CASH_FLOW_ENDPOINT, ticker, start_date, end_date))
    income = _index_by_fiscal_year(_fetch(INCOME_ENDPOINT, ticker, start_date, end_date))
    balance = _index_by_fiscal_year(_fetch(BALANCE_SHEET_ENDPOINT, ticker, start_date, end_date))

    # We can only compute ratios for years present in ALL three statements.
    common_years = sorted(set(cash_flow) & set(income) & set(balance))
    if not common_years:
        detail = {
            "ticker": ticker,
            "period": {"start": start_date, "end": end_date},
            "signal": "NO_DATA",
            "liquidity_score": None,
            "operational_score": None,
            "financial_score": None,
            "ocf_ratio": None,
            "fcf_coverage": None,
            "years_used": [],
            "inputs": {},
            "warnings": ["No annual filings found in all three statements for "
                         "this ticker / date range."],
        }
        return _summarise(detail, save_detail, detail_dir)

    # --- 2. Compute per-year ratios ---------------------------------------
    ocf_ratios = []            # OCF ratios we could compute
    fcf_coverages = []         # FCF coverage for years that HAVE obligations
    fcf_no_obligation = []     # FCF values for years with zero dividends+interest
    inputs = {}                # raw figures we used, per year (for transparency)

    for year in common_years:
        cf = cash_flow[year]
        inc = income[year]
        bs = balance[year]

        cfo = cf.get(FIELD_CFO)
        capex = cf.get(FIELD_CAPEX)
        dividends = cf.get(FIELD_DIVIDENDS)
        interest = inc.get(FIELD_INTEREST)
        current_liabilities = bs.get(FIELD_CURRENT_LIABILITIES)

        inputs[year] = {
            "cfo": cfo,
            "capex": capex,
            "dividends": dividends,
            "interest_expense": interest,
            "current_liabilities": current_liabilities,
        }

        # --- Operating Cash Flow Ratio = CFO / Current Liabilities ---
        if cfo is None or current_liabilities is None:
            warnings.append(f"{year}: missing CFO or current liabilities; "
                            "skipped OCF ratio.")
        elif current_liabilities <= 0:
            warnings.append(f"{year}: current liabilities <= 0; skipped OCF ratio.")
        else:
            ocf_ratios.append(cfo / current_liabilities)

        # --- FCF Coverage = (CFO - CAPEX) / (Dividends + Interest) ---
        if cfo is None or capex is None:
            warnings.append(f"{year}: missing CFO or CAPEX; skipped FCF coverage.")
        else:
            # CAPEX/dividends are reported as negative outflows on Massive;
            # abs() keeps the maths correct regardless of sign convention.
            fcf = cfo - abs(capex)
            dividends_paid = abs(dividends) if dividends is not None else 0.0
            interest_paid = abs(interest) if interest is not None else 0.0
            denominator = dividends_paid + interest_paid

            if denominator > 0:
                fcf_coverages.append(fcf / denominator)
            else:
                # No dividends and no interest => no payout obligations.
                fcf_no_obligation.append(fcf)
                warnings.append(f"{year}: no dividends or interest expense; "
                                "FCF coverage treated as obligation-free.")

    # --- 3. Average the ratios across the usable years --------------------
    # Operating liquidity score
    if ocf_ratios:
        avg_ocf = _mean(ocf_ratios)
        ocf_norm = _normalise(avg_ocf, OCF_ANCHORS)
    else:
        avg_ocf = None
        ocf_norm = None

    # Future liquidity score
    if fcf_coverages:
        avg_fcf = _mean(fcf_coverages)
        fcf_norm = _normalise(avg_fcf, FCF_ANCHORS)
    elif fcf_no_obligation:
        # Every usable year was obligation-free: there is nothing to cover.
        # Treat as STRONG if cash generation is non-negative, WEAK otherwise.
        avg_fcf = None  # no finite ratio to report
        fcf_norm = 1.0 if _mean(fcf_no_obligation) >= 0 else 0.0
    else:
        avg_fcf = None
        fcf_norm = None

    # --- 4. Combine into the composite score and final signal -------------
    if ocf_norm is None or fcf_norm is None:
        detail = {
            "ticker": ticker,
            "period": {"start": start_date, "end": end_date},
            "signal": "NO_DATA",
            "liquidity_score": None,
            "operational_score": round(ocf_norm, 4) if ocf_norm is not None else None,
            "financial_score": round(fcf_norm, 4) if fcf_norm is not None else None,
            "ocf_ratio": None,
            "fcf_coverage": None,
            "years_used": common_years,
            "inputs": inputs,
            "warnings": warnings + ["Could not compute one of the two sub-scores."],
        }
        return _summarise(detail, save_detail, detail_dir)

    composite = OCF_WEIGHT * ocf_norm + FCF_WEIGHT * fcf_norm
    signal = _band(composite, COMPOSITE_BANDS)

    detail = {
        "ticker": ticker,
        "period": {"start": start_date, "end": end_date},
        "signal": signal,
        "liquidity_score": round(composite, 4),
        "operational_score": round(ocf_norm, 4),
        "financial_score": round(fcf_norm, 4),
        "ocf_ratio": {
            "value": round(avg_ocf, 4) if avg_ocf is not None else None,
            "normalised": round(ocf_norm, 4),
            "band": _band(avg_ocf, OCF_BANDS) if avg_ocf is not None else "STRONG",
        },
        "fcf_coverage": {
            "value": round(avg_fcf, 4) if avg_fcf is not None else None,
            "normalised": round(fcf_norm, 4),
            "band": _band(avg_fcf, FCF_BANDS) if avg_fcf is not None
                    else ("STRONG" if fcf_norm >= 1.0 else "WEAK"),
        },
        "years_used": common_years,
        "inputs": inputs,
        "warnings": warnings,
    }
    return _summarise(detail, save_detail, detail_dir)
