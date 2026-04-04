"""Shared utilities for data model modules."""

from __future__ import annotations

from typing import SupportsFloat, cast


def safe_float(val: object, default: float = 0.0) -> float:
    """Safely coerce a DB row value to *float*.

    SQLite row dicts use ``dict[str, object]``, so ``.get()`` returns
    ``object``.  This helper narrows the type for *mypy --strict*.
    """
    if val is None:
        return default
    return float(cast("SupportsFloat", val))
