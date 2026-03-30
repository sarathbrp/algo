"""Position snapshot repository — position history audit trail."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import PositionSnapshot, TradeSide


def snapshot_positions(
    session: Session,
    *,
    user_id: str,
    positions: list[dict[str, Any]],
    mode: str | None = None,
) -> list[PositionSnapshot]:
    """Create one snapshot row per open position. Returns created snapshots."""
    snapshots = []
    for bp in positions:
        symbol = str(bp.get("symbol") or "").strip().upper()
        qty_raw = int(float(bp.get("qty") or 0))
        qty = abs(qty_raw)
        if not symbol or qty <= 0:
            continue
        side_raw = str(bp.get("side") or "long").strip().lower()
        side = side_raw if side_raw in ("long", "short") else ("short" if qty_raw < 0 else "long")
        market_value = float(bp.get("market_value") or 0)
        cost_basis = float(bp.get("cost_basis") or 0)
        current_price = abs(market_value / qty) if qty else None
        avg_entry_price = abs(cost_basis / qty_raw) if qty_raw else None
        unrealized_pnl = float(bp.get("unrealized_pl") or 0)

        snap = PositionSnapshot(
            user_id=user_id,
            symbol=symbol,
            side=TradeSide(side),
            qty=float(qty),
            avg_entry_price=avg_entry_price,
            current_price=current_price,
            unrealized_pnl=unrealized_pnl,
            mode=mode,
        )
        session.add(snap)
        snapshots.append(snap)
    return snapshots


def get_position_history(
    session: Session,
    user_id: str,
    *,
    symbol: str | None = None,
    mode: str | None = None,
    limit: int = 100,
) -> list[PositionSnapshot]:
    """Return position snapshots, newest first."""
    q = select(PositionSnapshot).where(PositionSnapshot.user_id == user_id)
    if symbol is not None:
        q = q.where(PositionSnapshot.symbol == symbol.upper())
    if mode is not None:
        q = q.where(PositionSnapshot.mode == mode)
    return list(session.scalars(q.order_by(PositionSnapshot.captured_at.desc()).limit(limit)))
