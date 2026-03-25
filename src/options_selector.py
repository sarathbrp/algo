"""
v1: pick one ATM series (14–35 DTE), call for bullish / put for bearish; validate spread + liquidity.

Chain discovery is broker-specific: pass `candidates` from your broker into `select_option_contract`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence


@dataclass(frozen=True)
class OptionContractCandidate:
    """One tradable series (e.g. from an options chain snapshot)."""

    symbol: str
    strike: float
    expiration: date
    right: str  # "call" | "put"
    open_interest: int
    volume: int
    bid: float
    ask: float


@dataclass(frozen=True)
class SelectedOptionContract:
    """Chosen contract after ATM + liquidity filters."""

    symbol: str
    strike: float
    expiration: date
    right: str
    bid: float
    ask: float
    mid: float
    spread_pct: float
    open_interest: int
    volume: int


def _contract_selection_cfg(config: dict[str, Any]) -> dict[str, Any]:
    o = config.get("options") or {}
    return o.get("contract_selection") or {}


def _days_to_expiry(exp: date, as_of: date) -> int:
    return max(0, (exp - as_of).days)


def _mid_spread(bid: float, ask: float) -> tuple[float, float]:
    if bid <= 0 or ask <= 0 or ask < bid:
        return 0.0, 999.0
    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
    return mid, spread_pct


def validate_option_liquidity(
    contract: SelectedOptionContract,
    config: dict[str, Any],
) -> tuple[bool, str]:
    """Return (ok, reason) using options.contract_selection thresholds."""
    cs = _contract_selection_cfg(config)
    max_spread = float(cs.get("max_bid_ask_spread_pct", 5.0))
    min_oi = int(cs.get("min_open_interest", 0))
    min_vol = int(cs.get("min_volume", 0))

    if contract.spread_pct > max_spread:
        return False, "spread %.2f%% > max %.2f%%" % (contract.spread_pct, max_spread)
    if contract.open_interest < min_oi:
        return False, "open_interest %d < min %d" % (contract.open_interest, min_oi)
    if contract.volume < min_vol:
        return False, "volume %d < min %d" % (contract.volume, min_vol)
    return True, "ok"


def select_option_contract(
    config: dict[str, Any],
    intent_underlying: str,
    intent_right: str,
    *,
    candidates: Sequence[OptionContractCandidate] | None,
    underlying_spot: float | None,
    as_of: date | None = None,
) -> tuple[SelectedOptionContract | None, str | None]:
    """
    Filter by underlying, call/put, DTE window; pick ATM by closest strike to spot.

    If `candidates` is None or empty, returns (None, reason) — wire broker chain here.
    If `underlying_spot` is None, cannot do ATM — returns (None, reason).
    """
    if not candidates:
        return None, "no contract candidates (integrate broker options chain)"

    u = str(intent_underlying or "").upper()
    want_right = str(intent_right or "").strip().lower()
    if want_right in ("calls", "call"):
        want_right = "call"
    elif want_right in ("puts", "put"):
        want_right = "put"
    else:
        return None, "invalid option right %r" % intent_right

    cs = _contract_selection_cfg(config)
    dte_min = int(cs.get("expiry_min_days", 14))
    dte_max = int(cs.get("expiry_max_days", 35))
    moneyness = str(cs.get("moneyness", "ATM")).strip().upper()
    if moneyness != "ATM":
        return None, "moneyness %r not supported (v1: ATM only)" % moneyness

    if underlying_spot is None or underlying_spot <= 0:
        return None, "underlying spot price required for ATM selection"

    as_of = as_of or date.today()

    filtered: list[OptionContractCandidate] = []
    for c in candidates:
        if _symbol_underlying(c.symbol) != u:
            continue
        cr = str(c.right or "").strip().lower()
        if cr in ("calls", "call"):
            cr = "call"
        elif cr in ("puts", "put"):
            cr = "put"
        else:
            continue
        if cr != want_right:
            continue
        dte = _days_to_expiry(c.expiration, as_of)
        if dte < dte_min or dte > dte_max:
            continue
        filtered.append(c)

    if not filtered:
        return None, "no contracts in DTE window [%d, %d] for %s %s" % (dte_min, dte_max, u, want_right)

    # ATM: minimize |strike - spot|
    best = min(filtered, key=lambda x: abs(float(x.strike) - float(underlying_spot)))
    mid, sp = _mid_spread(best.bid, best.ask)
    if mid <= 0:
        return None, "invalid quote for %s" % best.symbol

    selected = SelectedOptionContract(
        symbol=str(best.symbol).strip().upper(),
        strike=float(best.strike),
        expiration=best.expiration,
        right=want_right,
        bid=float(best.bid),
        ask=float(best.ask),
        mid=mid,
        spread_pct=sp,
        open_interest=int(best.open_interest),
        volume=int(best.volume),
    )
    ok, reason = validate_option_liquidity(selected, config)
    if not ok:
        return None, reason
    return selected, None


def _symbol_underlying(occ_symbol: str) -> str:
    """Best-effort OCC root (letters before first digit)."""
    s = str(occ_symbol or "").strip().upper()
    for i, ch in enumerate(s):
        if ch.isdigit():
            return s[:i] if i else s
    return s
