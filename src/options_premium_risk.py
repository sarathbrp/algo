"""
Options risk limits in **premium paid** (debit), not stock notional.

v1 sizing (brutal simple):
  max_premium_budget = min(per-trade % cap, room left under total % cap)
  contracts = max(0, floor(max_premium_budget / (option_mid * 100)))
  if contracts < 1 → skip "premium too expensive"
  then cap by options.v1_max_contracts_per_trade (default 1: one ATM call/put).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# OCC-style US option symbol: root + YYMMDD + C|P + 8-digit strike (thousandths)
_OCC_OPTION_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def is_option_symbol(symbol: str) -> bool:
    s = str(symbol or "").strip().upper().replace(" ", "")
    if not s or len(s) < 12:
        return False
    return bool(_OCC_OPTION_RE.match(s))


def sum_open_option_positions_premium(positions: list[dict[str, Any]] | None) -> float:
    """
    Sum premium paid for open **long** option positions (debit cost basis).

    Uses abs(cost_basis) when qty > 0 and symbol looks like an OCC option.
    """
    if not positions:
        return 0.0
    total = 0.0
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not is_option_symbol(sym):
            continue
        try:
            qty = int(float(p.get("qty") or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            cb = float(p.get("cost_basis") or 0)
        except (TypeError, ValueError):
            cb = 0.0
        total += abs(cb)
    return total


def count_open_long_option_positions(positions: list[dict[str, Any]] | None) -> int:
    """Number of distinct long option positions (OCC symbol, qty > 0)."""
    if not positions:
        return 0
    n = 0
    for p in positions:
        sym = str(p.get("symbol") or "")
        if not is_option_symbol(sym):
            continue
        try:
            qty = int(float(p.get("qty") or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            n += 1
    return n


def _options_pct_limits(config: dict[str, Any]) -> tuple[float, float]:
    o = config.get("options") or {}
    max_total = float(o.get("max_total_options_exposure_pct", 5)) / 100.0
    max_single = float(o.get("max_option_position_pct", 2)) / 100.0
    return max_total, max_single


@dataclass(frozen=True)
class OptionsPremiumGateResult:
    ok: bool
    reason: str | None
    contracts: int  # 0 if skip


def evaluate_options_premium_before_order(
    config: dict[str, Any],
    *,
    equity: float,
    positions: list[dict[str, Any]] | None,
    option_mid_price: float,
) -> OptionsPremiumGateResult:
    """
    v1: contracts = max(0, floor(max_premium_budget / (mid * 100)));
    if contracts < 1 → premium too expensive.

    max_premium_budget = min(single-trade cap, room under portfolio premium cap).
    """
    if equity <= 0:
        return OptionsPremiumGateResult(False, "equity <= 0", 0)
    if option_mid_price <= 0:
        return OptionsPremiumGateResult(False, "invalid option mid", 0)

    opts = config.get("options") or {}
    max_total_frac, max_single_frac = _options_pct_limits(config)
    cap_total = equity * max_total_frac
    cap_single = equity * max_single_frac
    mult = 100.0
    per_contract = option_mid_price * mult

    total_existing = sum_open_option_positions_premium(positions)
    room_total = cap_total - total_existing
    max_premium_budget = min(cap_single, max(0.0, room_total))

    if max_premium_budget <= 0:
        return OptionsPremiumGateResult(
            False,
            "options exposure cap (no premium budget left; existing $%.2f vs cap $%.2f)"
            % (total_existing, cap_total),
            0,
        )

    contracts = int(max_premium_budget // per_contract)
    contracts = max(0, contracts)
    if contracts < 1:
        return OptionsPremiumGateResult(
            False,
            "premium too expensive (budget $%.2f, 1 contract ≈ $%.2f at mid)"
            % (max_premium_budget, per_contract),
            0,
        )

    v1_max = int(opts.get("v1_max_contracts_per_trade", 1))
    if v1_max < 1:
        v1_max = 1
    contracts = min(contracts, v1_max)

    new_premium = contracts * per_contract
    if total_existing + new_premium > cap_total + 1e-6:
        return OptionsPremiumGateResult(
            False,
            "options exposure cap (existing $%.2f + new $%.2f > $%.2f)"
            % (total_existing, new_premium, cap_total),
            0,
        )

    return OptionsPremiumGateResult(True, None, contracts)
