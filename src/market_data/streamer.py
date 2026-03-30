"""Helpers for shared market-data streaming pipelines."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.market_data.quote_cache import QuoteSnapshot, RedisQuoteCache
from src.market_data.symbols import aggregate_symbols


def build_subscription_set(*collections: list[str]) -> list[str]:
    """Return the canonical set of symbols a streamer should subscribe to."""
    return aggregate_symbols(*collections)


def normalize_quote_update(
    symbol: str,
    *,
    bid: float,
    ask: float,
    timestamp: datetime | None = None,
    source: str = "alpaca",
) -> QuoteSnapshot:
    ts = timestamp or datetime.now(timezone.utc)
    mid = (float(bid) + float(ask)) / 2.0
    spread_pct = 0.0 if mid <= 0 else ((float(ask) - float(bid)) / mid) * 100.0
    return QuoteSnapshot(
        symbol=str(symbol).strip().upper(),
        bid=float(bid),
        ask=float(ask),
        mid=float(mid),
        spread_pct=float(spread_pct),
        timestamp=ts,
        source=source,
    )


class QuoteUpdateProcessor:
    """Stores normalized quote updates in the shared cache."""

    def __init__(self, cache: RedisQuoteCache) -> None:
        self._cache = cache

    def process(self, symbol: str, *, bid: float, ask: float, timestamp: datetime | None = None, source: str = "alpaca") -> QuoteSnapshot:
        snapshot = normalize_quote_update(
            symbol,
            bid=bid,
            ask=ask,
            timestamp=timestamp,
            source=source,
        )
        self._cache.set_quote(snapshot)
        self._cache.set_feed_status("OK", as_of=snapshot.timestamp)
        return snapshot

    def mark_stale(self, *, as_of: datetime | None = None) -> None:
        self._cache.set_feed_status("STALE", as_of=as_of)

    def mark_error(self, *, as_of: datetime | None = None) -> None:
        self._cache.set_feed_status("ERROR", as_of=as_of)
