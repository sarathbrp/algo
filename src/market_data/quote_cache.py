"""Redis-backed cache helpers for shared latest market quotes."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    bid: float
    ask: float
    mid: float
    spread_pct: float
    timestamp: datetime
    source: str = "alpaca"

    def is_stale(self, *, now: datetime | None = None, max_age_seconds: float = 15.0) -> bool:
        now = now or _utcnow()
        return (now - self.timestamp).total_seconds() > max_age_seconds

    def to_json(self) -> str:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str | bytes | None) -> QuoteSnapshot | None:
        if payload in (None, b"", ""):
            return None
        raw = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        return cls(
            symbol=str(raw["symbol"]).upper(),
            bid=float(raw["bid"]),
            ask=float(raw["ask"]),
            mid=float(raw["mid"]),
            spread_pct=float(raw["spread_pct"]),
            timestamp=datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00")),
            source=str(raw.get("source") or "alpaca"),
        )


class RedisQuoteCache:
    """Thin cache wrapper over a Redis-style client."""

    def __init__(self, client: Any, *, namespace: str = "market") -> None:
        self._client = client
        self._namespace = namespace.strip(":")

    def _quote_key(self, symbol: str) -> str:
        return f"{self._namespace}:quote:{symbol.upper()}"

    def _symbols_key(self) -> str:
        return f"{self._namespace}:symbols:active"

    def _feed_status_key(self) -> str:
        return f"{self._namespace}:feed:status"

    def _feed_ts_key(self) -> str:
        return f"{self._namespace}:feed:last_update_ts"

    def set_quote(self, snapshot: QuoteSnapshot) -> None:
        self._client.set(self._quote_key(snapshot.symbol), snapshot.to_json())
        self._client.sadd(self._symbols_key(), snapshot.symbol.upper())
        self._client.set(self._feed_ts_key(), snapshot.timestamp.isoformat())

    def get_quote(self, symbol: str) -> QuoteSnapshot | None:
        return QuoteSnapshot.from_json(self._client.get(self._quote_key(symbol)))

    def add_symbols(self, symbols: list[str]) -> None:
        normalized = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        if normalized:
            self._client.sadd(self._symbols_key(), *normalized)

    def remove_symbol(self, symbol: str) -> None:
        self._client.srem(self._symbols_key(), str(symbol).strip().upper())

    def list_active_symbols(self) -> list[str]:
        raw = self._client.smembers(self._symbols_key()) or set()
        return sorted(
            (item.decode() if isinstance(item, bytes) else str(item)).upper()
            for item in raw
        )

    def set_feed_status(self, status: str, *, as_of: datetime | None = None) -> None:
        stamp = (as_of or _utcnow()).isoformat()
        self._client.set(self._feed_status_key(), status)
        self._client.set(self._feed_ts_key(), stamp)

    def get_feed_status(self) -> tuple[str | None, datetime | None]:
        status = self._client.get(self._feed_status_key())
        stamp = self._client.get(self._feed_ts_key())
        normalized_status = None
        if status not in (None, b"", ""):
            normalized_status = status.decode() if isinstance(status, bytes) else str(status)
        normalized_ts = None
        if stamp not in (None, b"", ""):
            normalized_ts = datetime.fromisoformat((stamp.decode() if isinstance(stamp, bytes) else str(stamp)).replace("Z", "+00:00"))
        return normalized_status, normalized_ts
