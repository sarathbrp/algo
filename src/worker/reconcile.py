"""Reconciliation helpers for broker state versus durable DB state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.db import get_session
from src.db.repos import portfolio_repo


@dataclass(frozen=True)
class PositionReconcileResult:
    created: int
    updated: int
    removed: int
    total_broker_positions: int


def _normalize_side(raw_side: Any, qty_raw: int) -> str:
    side = str(raw_side or "long").strip().lower()
    if side in {"long", "short"}:
        return side
    return "short" if qty_raw < 0 else "long"


def reconcile_positions(
    user_id: str,
    broker_positions: list[dict[str, Any]],
    *,
    default_stop_pct: float = 1.5,
) -> PositionReconcileResult:
    """Reconcile broker-reported positions into the DB-backed position store."""
    with get_session() as session:
        existing = {p.symbol.upper(): p for p in portfolio_repo.get_positions(session, user_id)}
        seen_symbols: set[str] = set()
        created = 0
        updated = 0

        for broker_pos in broker_positions:
            symbol = str(broker_pos.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            qty_raw = int(float(broker_pos.get("qty") or 0))
            qty = abs(qty_raw)
            if qty <= 0:
                continue

            seen_symbols.add(symbol)
            current = existing.get(symbol)
            side = _normalize_side(broker_pos.get("side"), qty_raw)
            market_value = float(broker_pos.get("market_value") or 0)
            cost_basis = float(broker_pos.get("cost_basis") or 0)
            current_price = abs(market_value / qty) if qty else None
            avg_entry_price = abs(cost_basis / qty_raw) if qty_raw else None

            portfolio_repo.upsert_position(
                session,
                user_id=user_id,
                symbol=symbol,
                side=side,
                qty=float(qty),
                avg_entry_price=avg_entry_price,
                last_buy_price=(
                    float(current.last_buy_price)
                    if current is not None and getattr(current, "last_buy_price", None) is not None
                    else avg_entry_price
                ),
                current_price=current_price,
                unrealized_pnl=float(broker_pos.get("unrealized_pl") or 0),
                stop_pct=float(current.stop_pct) if current is not None and current.stop_pct is not None else default_stop_pct,
                partial_taken=bool(current.partial_taken) if current is not None else False,
                trail_high=float(current.trail_high) if current is not None and current.trail_high is not None else None,
                entered_at=current.entered_at if current is not None else datetime.now(timezone.utc),
            )
            if current is None:
                created += 1
            else:
                updated += 1

        removed = 0
        for symbol in set(existing) - seen_symbols:
            removed += int(portfolio_repo.remove_position(session, user_id, symbol))

        return PositionReconcileResult(
            created=created,
            updated=updated,
            removed=removed,
            total_broker_positions=len(seen_symbols),
        )
