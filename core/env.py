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
