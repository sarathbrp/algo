"""Helpers for aggregating shared market-data subscriptions."""
from __future__ import annotations

from typing import Iterable


def aggregate_symbols(*collections: Iterable[str]) -> list[str]:
    """Return the sorted union of non-empty uppercase symbols."""
    seen: set[str] = set()
    for collection in collections:
        for symbol in collection:
            normalized = str(symbol or "").strip().upper()
            if normalized:
                seen.add(normalized)
    return sorted(seen)
