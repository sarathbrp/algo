"""Trade repository."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Trade, TradeSide


def record_trade(
    session: Session,
    *,
    user_id: str,
    symbol: str,
    side: str = "long",
    qty: float,
    entry_price: float | None = None,
    exit_price: float | None = None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
    exit_reason: str | None = None,
    entered_at: datetime | None = None,
    exited_at: datetime | None = None,
    setup_vector: list[float] | None = None,
    mode: str | None = None,
) -> Trade:
    """Insert a completed trade record and return it."""
    trade = Trade(
        user_id=user_id,
        symbol=symbol.upper(),
        side=TradeSide(side.lower()),
        qty=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        mode=mode,
        entered_at=entered_at,
        exited_at=exited_at,
        setup_vector=setup_vector,
    )
    session.add(trade)
    return trade


def get_trades(session: Session, user_id: str, limit: int = 50, *, mode: str | None = None) -> list[Trade]:
    """Return the most recent *limit* trades for *user_id*, newest first."""
    q = select(Trade).where(Trade.user_id == user_id)
    if mode is not None:
        q = q.where(Trade.mode == mode)
    return list(session.scalars(q.order_by(Trade.exited_at.desc(), Trade.id.desc()).limit(limit)))


def get_trades_for_date(
    session: Session, user_id: str, date, *, mode: str | None = None
) -> list[Trade]:
    """Return all trades closed on a specific date."""
    from datetime import timedelta, timezone as tz
    day_start = datetime(date.year, date.month, date.day, tzinfo=tz.utc)
    day_end = day_start + timedelta(days=1)
    q = (
        select(Trade)
        .where(
            Trade.user_id == user_id,
            Trade.exited_at >= day_start,
            Trade.exited_at < day_end,
        )
    )
    if mode is not None:
        q = q.where(Trade.mode == mode)
    return list(session.scalars(q.order_by(Trade.exited_at.asc())))


def get_trade_by_id(session: Session, trade_id: int) -> Trade | None:
    return session.get(Trade, trade_id)
