"""Registry of per-person analysis modules.

Each module under `modules/` should expose:

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

Modules that don't follow this shape are bridged via core/adapters.py.
Import errors (e.g. a teammate's uninstalled dependency) are captured per
module so one broken file can't take down the whole run.
"""
from __future__ import annotations

import importlib

OWNERS = ["samar", "aarav", "aarav2", "justin", "anshu", "anshu2", "cosmo", "diya", "kavin"]


def load_signals() -> list[dict]:
    """One entry per owner with metadata and how to run it.

    Each entry: {
        "module_name": str,
        "name": str, "owner": str, "category": str,
        "module": ModuleType | None,
        "adapter": dict | None,        # from ADAPTERS if the module is wrapped
        "error": Exception | None,     # import failure, if any
    }
    """
    from core.adapters import ADAPTERS

    entries = []
    for owner in OWNERS:
        module = None
        error = None
        try:
            module = importlib.import_module(f"modules.{owner}")
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - capture, incl. sys.exit() at import
            error = exc

        adapter = ADAPTERS.get(owner)
        if adapter:
            name, owner_name, category = adapter["name"], adapter["owner"], adapter["category"]
        elif module is not None:
            name = getattr(module, "SIGNAL_NAME", owner)
            owner_name = getattr(module, "SIGNAL_OWNER", owner)
            category = getattr(module, "SIGNAL_CATEGORY", "Other")
        else:
            name, owner_name, category = owner, owner, "Other"

        entries.append({
            "module_name": owner,
            "name": name,
            "owner": owner_name,
            "category": category,
            "module": module,
            "adapter": adapter,
            "error": error,
        })
    return entries


def signal_specs() -> list[dict]:
    """Metadata for every signal, without running any analysis.

    Returns a list of {"module", "name", "owner", "category"} so a UI can
    build its columns/panels up front.
    """
    return [
        {"module": e["module_name"], "name": e["name"],
         "owner": e["owner"], "category": e["category"]}
        for e in load_signals()
    ]
