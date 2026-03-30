"""Tests for shared market-data cache and streamer helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.market_data.quote_cache import QuoteSnapshot, RedisQuoteCache
from src.market_data.streamer import QuoteUpdateProcessor, build_subscription_set, normalize_quote_update
from src.market_data.symbols import aggregate_symbols


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    def get(self, key: str):
        return self.kv.get(key)

    def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    def smembers(self, key: str):
        return self.sets.get(key, set())

    def srem(self, key: str, value: str) -> None:
        self.sets.setdefault(key, set()).discard(value)


def test_aggregate_symbols_normalizes_and_sorts():
    result = aggregate_symbols(["aapl", "spy", ""], ["SPY", " nvda "], [])
    assert result == ["AAPL", "NVDA", "SPY"]


def test_build_subscription_set_is_union():
    symbols = build_subscription_set(["AAPL", "MSFT"], ["msft", "QQQ"])
    assert symbols == ["AAPL", "MSFT", "QQQ"]


def test_quote_snapshot_json_round_trip():
    snapshot = QuoteSnapshot(
        symbol="AAPL",
        bid=100.0,
        ask=101.0,
        mid=100.5,
        spread_pct=0.995,
        timestamp=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
    )
    decoded = QuoteSnapshot.from_json(snapshot.to_json())
    assert decoded == snapshot


def test_quote_snapshot_staleness():
    snapshot = QuoteSnapshot(
        symbol="AAPL",
        bid=100.0,
        ask=101.0,
        mid=100.5,
        spread_pct=0.995,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    assert snapshot.is_stale(max_age_seconds=15) is True
    assert snapshot.is_stale(max_age_seconds=60) is False


def test_redis_quote_cache_stores_and_reads_quote():
    cache = RedisQuoteCache(FakeRedis())
    snapshot = QuoteSnapshot(
        symbol="AAPL",
        bid=100.0,
        ask=101.0,
        mid=100.5,
        spread_pct=0.995,
        timestamp=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
    )
    cache.set_quote(snapshot)
    loaded = cache.get_quote("AAPL")
    assert loaded == snapshot
    assert cache.list_active_symbols() == ["AAPL"]


def test_redis_quote_cache_symbol_management():
    cache = RedisQuoteCache(FakeRedis())
    cache.add_symbols(["AAPL", "qqq", ""])
    assert cache.list_active_symbols() == ["AAPL", "QQQ"]
    cache.remove_symbol("aapl")
    assert cache.list_active_symbols() == ["QQQ"]


def test_redis_quote_cache_feed_status():
    cache = RedisQuoteCache(FakeRedis())
    ts = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    cache.set_feed_status("STALE", as_of=ts)
    status, stamp = cache.get_feed_status()
    assert status == "STALE"
    assert stamp == ts


def test_normalize_quote_update_computes_mid_and_spread():
    snapshot = normalize_quote_update("AAPL", bid=100.0, ask=102.0, timestamp=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc))
    assert snapshot.symbol == "AAPL"
    assert snapshot.mid == 101.0
    assert round(snapshot.spread_pct, 4) == round((2.0 / 101.0) * 100.0, 4)


def test_quote_update_processor_writes_cache_and_status():
    cache = RedisQuoteCache(FakeRedis())
    processor = QuoteUpdateProcessor(cache)
    ts = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    snapshot = processor.process("AAPL", bid=100.0, ask=101.0, timestamp=ts)

    assert cache.get_quote("AAPL") == snapshot
    status, stamp = cache.get_feed_status()
    assert status == "OK"
    assert stamp == ts


def test_quote_update_processor_marks_stale_and_error():
    cache = RedisQuoteCache(FakeRedis())
    processor = QuoteUpdateProcessor(cache)
    ts = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    processor.mark_stale(as_of=ts)
    assert cache.get_feed_status() == ("STALE", ts)
    processor.mark_error(as_of=ts)
    assert cache.get_feed_status() == ("ERROR", ts)
