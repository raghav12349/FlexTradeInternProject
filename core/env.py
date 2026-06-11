"""Optionally load API keys from a gitignored `.keys.env` at the repo root.

Format (one per line):
    POLYGON_API_KEY=...
    MASSIVE_API_KEY=...

Real environment variables take precedence (we only fill in what's missing),
and the file is gitignored so keys never get committed.
"""
from __future__ import annotations

import os
from pathlib import Path

_KEYS_FILE = Path(__file__).resolve().parent.parent / ".keys.env"


def load_local_keys() -> None:
    if not _KEYS_FILE.exists():
        return
    for line in _KEYS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_key(name: str) -> str:
    """Return an API key, loading .keys.env first. Raises if it's not set.

    Convenience for modules that want a single call (e.g. kavin):
        from core.env import get_key
        key = get_key("MASSIVE_API_KEY")
    """
    load_local_keys()
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (export it or add it to .keys.env)")
    return value
