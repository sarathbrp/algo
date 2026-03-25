"""
Cap contracts by premium budget and place option orders.

Uses options_premium_risk for portfolio + per-trade premium caps; uses
ExecutionManager.build_order + broker.submit_order for the actual send.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from .options_premium_risk import (
    count_open_long_option_positions,
    evaluate_options_premium_before_order,
)
from .options_selector import SelectedOptionContract

if TYPE_CHECKING:
    from .execution import ExecutionManager


@dataclass(frozen=True)
class PreparedOptionOrder:
    occ_symbol: str
    contracts: int
    mid: float
    spread_pct: float


def max_open_option_positions_limit(config: dict[str, Any]) -> int:
    o = config.get("options") or {}
    return int(o.get("max_open_option_positions", 1))


def prepare_option_order_premium_only(
    config: dict[str, Any],
    *,
    equity: float,
    positions: list[dict[str, Any]] | None,
    selected: SelectedOptionContract,
) -> tuple[PreparedOptionOrder | None, str | None]:
    """
    Apply max open positions + v1 premium-budget contract count; return prepared order or (None, reason).
    """
    opts = config.get("options") or {}
    if not bool(opts.get("enabled")):
        return None, "options disabled"

    max_open = max_open_option_positions_limit(config)
    if count_open_long_option_positions(positions) >= max_open:
        return None, "max open option positions (%d)" % max_open

    gate = evaluate_options_premium_before_order(
        config,
        equity=float(equity),
        positions=positions,
        option_mid_price=float(selected.mid),
    )
    if not gate.ok:
        return None, gate.reason

    return PreparedOptionOrder(
        occ_symbol=selected.symbol,
        contracts=int(gate.contracts),
        mid=float(selected.mid),
        spread_pct=float(selected.spread_pct),
    ), None


def place_option_order(
    broker: Any,
    execution: "ExecutionManager",
    prepared: PreparedOptionOrder,
) -> tuple[Any | None, str | None]:
    """
    Build limit/market order via ExecutionManager and submit on broker.

    broker must implement submit_order(OrderRequest).
    """
    req = execution.build_order(
        prepared.occ_symbol,
        "buy",
        prepared.contracts,
        prepared.mid,
        prepared.spread_pct,
    )
    if req is None:
        return None, "execution could not build order (spread gate)"
    try:
        out = broker.submit_order(req)
        return out, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])
