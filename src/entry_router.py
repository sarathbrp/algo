"""
Route new entries between options and existing stock execution.

Stock sizing and risk rules are unchanged. Options flow:
  options_adapter → options_selector → options_execution (premium caps + place order).
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .options_adapter import adapt_stock_signal_to_option_intent
from .options_execution import (
    place_option_order,
    prepare_option_order_premium_only,
)
from .options_selector import OptionContractCandidate, select_option_contract


@dataclass(frozen=True)
class EntryRouteSignal:
    """Semantic signal for routing — does not replace equity EntrySignal / sizing."""

    underlying: str
    direction: str  # "bullish" | "bearish" (maps to call / put per config entry_mapping)
    source: str  # "trend_long" | "bear_etf" | "news_override"
    stock_symbol: str | None = None  # equity leg if different (e.g. SQQQ vs QQQ option)


def should_use_options(config: dict[str, Any], signal: EntryRouteSignal) -> bool:
    """Whether this signal may use the options executor (contract selection TBD)."""
    opts = config.get("options") or {}
    if not bool(opts.get("enabled")):
        return False
    mode = str(opts.get("mode") or "").strip().lower()
    if mode != "long_premium_only":
        return False
    allowed = {str(x).upper() for x in (opts.get("allowed_underlyings") or [])}
    u = str(signal.underlying or "").upper()
    if u not in allowed:
        return False
    d = str(signal.direction or "").lower()
    if d not in ("bullish", "bearish"):
        return False
    mapping = opts.get("entry_mapping") or {}
    if d == "bullish" and not mapping.get("bullish_signal"):
        return False
    if d == "bearish" and not mapping.get("bearish_signal"):
        return False
    return True


def _options_skip_msg(log_dt: Any | None, underlying: str, reason: str) -> None:
    try:
        ts = log_dt.strftime("%H:%M ET") if log_dt is not None else ""
    except Exception:
        ts = ""
    label = str(underlying or "OPTIONS").upper()
    if ts:
        print(ts, f"{label} skip — {reason}")
    else:
        print(f"{label} skip — {reason}")


def route_to_options_executor(
    config: dict[str, Any],
    signal: EntryRouteSignal,
    *,
    log_dt: Any | None = None,
    verbose: bool = False,
    account_equity: float | None = None,
    positions: list[dict[str, Any]] | None = None,
    broker: Any | None = None,
    execution_manager: Any | None = None,
    chain_candidates: Sequence[OptionContractCandidate] | None = None,
    underlying_spot: float | None = None,
) -> bool:
    """
    Attempt options execution. Returns True if an option order was placed (skip stock).

    Pipeline: adapt signal → select contract (chain + liquidity) → premium caps → place order.
    Without `chain_candidates`, selection fails until a broker chain feed is wired.
    Without `broker` / `execution_manager`, preparation may succeed but nothing is sent.
    """
    intent = adapt_stock_signal_to_option_intent(
        config,
        underlying=signal.underlying,
        direction=signal.direction,
        source=signal.source,
        stock_symbol=signal.stock_symbol,
    )
    if intent is None:
        if verbose and log_dt is not None:
            try:
                ts = log_dt.strftime("%H:%M ET")
            except Exception:
                ts = ""
            print(
                ts,
                "OPTIONS",
                signal.underlying,
                "— not mapped to option intent (%s %s); using stock path"
                % (signal.direction, signal.source),
            )
        return False

    selected, sel_err = select_option_contract(
        config,
        intent.underlying,
        intent.right,
        candidates=chain_candidates,
        underlying_spot=underlying_spot,
    )
    if selected is None:
        _options_skip_msg(log_dt, signal.underlying, sel_err or "selector")
        return False

    if account_equity is None or positions is None:
        _options_skip_msg(log_dt, signal.underlying, "missing account_equity or positions for premium caps")
        return False

    prepared, prep_err = prepare_option_order_premium_only(
        config,
        equity=float(account_equity),
        positions=positions,
        selected=selected,
    )
    if prepared is None:
        _options_skip_msg(log_dt, signal.underlying, prep_err or "premium / position limits")
        return False

    if broker is not None and execution_manager is not None:
        order, place_err = place_option_order(broker, execution_manager, prepared)
        if place_err:
            _options_skip_msg(log_dt, signal.underlying, place_err)
            return False
        try:
            ts = log_dt.strftime("%H:%M ET") if log_dt is not None else ""
        except Exception:
            ts = ""
        oid = getattr(order, "id", "") if order is not None else ""
        if ts:
            print(
                ts,
                prepared.occ_symbol,
                "BUY",
                prepared.contracts,
                "contracts (options)",
                oid,
            )
        else:
            print(prepared.occ_symbol, "BUY", prepared.contracts, "contracts (options)", oid)
        return True

    if verbose and log_dt is not None:
        try:
            ts = log_dt.strftime("%H:%M ET")
        except Exception:
            ts = ""
        print(
            ts,
            "OPTIONS",
            signal.underlying,
            "— prepared %s x%d but broker/execution not wired; using stock path"
            % (prepared.occ_symbol, prepared.contracts),
        )
    return False


def route_to_stock_executor(signal: EntryRouteSignal, execute_stock: Callable[[], None]) -> None:
    """Unchanged stock submission / tracking (caller supplies closure)."""
    execute_stock()
