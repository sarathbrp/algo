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


def _options_ineligible_reason(config: dict[str, Any], signal: EntryRouteSignal) -> str:
    """Human reason when options.enabled but should_use_options is false (for logging)."""
    opts = config.get("options") or {}
    mode = str(opts.get("mode") or "").strip().lower()
    if mode != "long_premium_only":
        return "options.mode is %r (need long_premium_only)" % (opts.get("mode"),)
    allowed = {str(x).upper() for x in (opts.get("allowed_underlyings") or [])}
    u = str(signal.underlying or "").upper()
    if not allowed:
        return "allowed_underlyings is empty"
    if u not in allowed:
        return "underlying %s not in allowed_underlyings (%s)" % (u, ",".join(sorted(allowed)))
    d = str(signal.direction or "").lower()
    if d not in ("bullish", "bearish"):
        return "direction %r not bullish/bearish" % (signal.direction,)
    mapping = opts.get("entry_mapping") or {}
    if d == "bullish" and not mapping.get("bullish_signal"):
        return "entry_mapping.bullish_signal missing"
    if d == "bearish" and not mapping.get("bearish_signal"):
        return "entry_mapping.bearish_signal missing"
    return "options routing not eligible"


def log_options_stock_path_if_ineligible(
    config: dict[str, Any],
    signal: EntryRouteSignal,
    log_dt: Any | None,
) -> None:
    """If options are on but this signal uses stock only, log why (once per call site)."""
    opts = config.get("options") or {}
    if not bool(opts.get("enabled")):
        return
    if should_use_options(config, signal):
        return
    _options_skip_msg(log_dt, signal.underlying, "stock path: " + _options_ineligible_reason(config, signal))


def _options_skip_msg(log_dt: Any | None, underlying: str, reason: str) -> None:
    try:
        ts = log_dt.strftime("%H:%M ET") if log_dt is not None else ""
    except Exception:
        ts = ""
    label = str(underlying or "OPTIONS").upper()
    if ts:
        print(ts, f"{label} skip — {reason}", flush=True)
    else:
        print(f"{label} skip — {reason}", flush=True)


def _options_info(log_dt: Any | None, line: str) -> None:
    try:
        ts = log_dt.strftime("%H:%M ET") if log_dt is not None else ""
    except Exception:
        ts = ""
    if ts:
        print(ts, "OPTIONS —", line, flush=True)
    else:
        print("OPTIONS —", line, flush=True)


def _options_post_chain_skip(
    log_dt: Any | None,
    underlying: str,
    phase: str,
    reason: str,
    *,
    detail: str | None = None,
) -> None:
    """Log skip after chain fetch / try line: phase + exact reason, optional contract/context detail."""
    msg = "post-chain [%s]: %s" % (phase, reason)
    if detail:
        msg = "%s | %s" % (msg, detail)
    _options_skip_msg(log_dt, underlying, msg)


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
    Pass `chain_candidates` from the broker (e.g. Alpaca `get_option_chain_candidates`); empty list skips selection.
    Without `broker` / `execution_manager`, preparation may succeed but nothing is sent.
    """
    intent, adapt_err = adapt_stock_signal_to_option_intent(
        config,
        underlying=signal.underlying,
        direction=signal.direction,
        source=signal.source,
        stock_symbol=signal.stock_symbol,
    )
    if intent is None:
        _options_skip_msg(
            log_dt,
            signal.underlying,
            adapt_err or "could not build option intent",
        )
        return False

    n_chain = len(chain_candidates) if chain_candidates is not None else 0
    _options_info(
        log_dt,
        "try %s %s (%s) | chain=%d contracts"
        % (intent.underlying, intent.right, intent.source, n_chain),
    )

    selected, sel_err = select_option_contract(
        config,
        intent.underlying,
        intent.right,
        candidates=chain_candidates,
        underlying_spot=underlying_spot,
    )
    if selected is None:
        spot_s = (
            "%.6g" % float(underlying_spot)
            if underlying_spot is not None and float(underlying_spot) > 0
            else repr(underlying_spot)
        )
        _options_post_chain_skip(
            log_dt,
            signal.underlying,
            "select",
            sel_err if sel_err is not None else "(select_option_contract returned None with no reason string)",
            detail="chain_n=%d spot=%s want=%s %s" % (n_chain, spot_s, intent.underlying, intent.right),
        )
        return False

    if account_equity is None or positions is None:
        _options_post_chain_skip(
            log_dt,
            signal.underlying,
            "premium_inputs",
            "missing account_equity or positions for premium caps",
            detail="picked=%s" % selected.symbol,
        )
        return False

    prepared, prep_err = prepare_option_order_premium_only(
        config,
        equity=float(account_equity),
        positions=positions,
        selected=selected,
    )
    if prepared is None:
        _options_post_chain_skip(
            log_dt,
            signal.underlying,
            "prepare",
            prep_err if prep_err is not None else "(prepare_option_order_premium_only returned None with no reason)",
            detail="picked=%s strike=%s exp=%s mid=%.6g spread_pct=%.4g"
            % (
                selected.symbol,
                selected.strike,
                selected.expiration.isoformat(),
                selected.mid,
                selected.spread_pct,
            ),
        )
        return False

    if broker is not None and execution_manager is not None:
        order, place_err = place_option_order(broker, execution_manager, prepared)
        if place_err:
            _options_post_chain_skip(
                log_dt,
                signal.underlying,
                "place",
                place_err if place_err is not None else "(place_option_order returned no error string)",
                detail="%s x%d @ mid=%.6g" % (prepared.occ_symbol, prepared.contracts, prepared.mid),
            )
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
                flush=True,
            )
        else:
            print(
                prepared.occ_symbol,
                "BUY",
                prepared.contracts,
                "contracts (options)",
                oid,
                flush=True,
            )
        return True

    _options_post_chain_skip(
        log_dt,
        signal.underlying,
        "submit",
        "broker or execution_manager was None after prepare",
        detail="%s x%d" % (prepared.occ_symbol, prepared.contracts),
    )
    return False


def route_to_stock_executor(signal: EntryRouteSignal, execute_stock: Callable[[], None]) -> None:
    """Unchanged stock submission / tracking (caller supplies closure)."""
    execute_stock()
