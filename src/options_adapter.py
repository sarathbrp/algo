"""
v1: bullish stock signal → long ATM call; bearish → long ATM put (long_premium_only).

Uses config options.entry_mapping and allowed_underlyings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

OptionRight = Literal["call", "put"]


@dataclass(frozen=True)
class OptionIntent:
    """What to buy: long call or long put on underlying (premium debit only for v1)."""

    underlying: str
    right: OptionRight
    source: str
    stock_symbol: str | None = None


def adapt_stock_signal_to_option_intent(
    config: dict[str, Any],
    *,
    underlying: str,
    direction: str,
    source: str,
    stock_symbol: str | None = None,
) -> OptionIntent | None:
    """
    Map bullish/bearish stock signal to call/put per config.

    direction: "bullish" → bullish_signal mapping (e.g. call)
               "bearish" → bearish_signal mapping (e.g. put)
    """
    opts = config.get("options") or {}
    if not bool(opts.get("enabled")):
        return None
    mode = str(opts.get("mode") or "").strip().lower()
    if mode != "long_premium_only":
        return None

    d = str(direction or "").strip().lower()
    mapping = opts.get("entry_mapping") or {}
    if d == "bullish":
        leg = str(mapping.get("bullish_signal") or "").strip().lower()
        if leg not in ("call", "calls"):
            return None
        right: OptionRight = "call"
    elif d == "bearish":
        leg = str(mapping.get("bearish_signal") or "").strip().lower()
        if leg not in ("put", "puts"):
            return None
        right = "put"
    else:
        return None

    u = str(underlying or "").strip().upper()
    if not u:
        return None
    allowed = {str(x).upper() for x in (opts.get("allowed_underlyings") or [])}
    if not allowed or u not in allowed:
        return None

    return OptionIntent(
        underlying=u,
        right=right,
        source=str(source or ""),
        stock_symbol=str(stock_symbol).upper() if stock_symbol else None,
    )
