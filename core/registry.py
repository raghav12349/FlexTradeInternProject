"""Registry of per-person analysis modules.

Each module under `modules/` must expose:

    SIGNAL_NAME: str      column label in the output table
    SIGNAL_OWNER: str     person responsible for the signal
    SIGNAL_CATEGORY: str  optional grouping, e.g. "Fundamentals" / "Technicals"
    analyze(ticker: str, period: str = "2y", **kwargs) -> dict
        returns: {
            "ticker":  str,
            "signal":  str,            # == SIGNAL_NAME
            "score":   float,          # normalized to [-1, 1]
            "rating":  str,            # one of core.rating.RATINGS
            "details": dict,           # arbitrary per-signal breakdown
        }

If a module hasn't been built yet, `analyze` may raise NotImplementedError
and the runner will simply leave its column blank.
"""
from __future__ import annotations

import importlib
from types import ModuleType

OWNERS = ["samar", "aarav", "justin", "anshu", "cosmo", "diya", "kavin"]


def load_modules() -> list[ModuleType]:
    return [importlib.import_module(f"modules.{owner}") for owner in OWNERS]


def module_meta(mod) -> dict:
    """Signal metadata for a module, preferring an adapter entry if one exists.

    Adapter-wrapped modules (e.g. aarav) don't define SIGNAL_* constants, so we
    fall back to the adapter's declared name/owner/category.
    """
    from core.adapters import ADAPTERS

    short = mod.__name__.split(".")[-1]
    ad = ADAPTERS.get(short)
    if ad:
        return {"module": short, "name": ad["name"], "owner": ad["owner"],
                "category": ad["category"]}
    return {
        "module": short,
        "name": getattr(mod, "SIGNAL_NAME", short),
        "owner": getattr(mod, "SIGNAL_OWNER", "?"),
        "category": getattr(mod, "SIGNAL_CATEGORY", "Other"),
    }


def signal_specs() -> list[dict]:
    """Metadata for every signal, without running any analysis.

    Returns a list of {"module", "name", "owner", "category"} so a UI can
    build its columns/panels up front.
    """
    return [module_meta(mod) for mod in load_modules()]
