"""Order log repository — buy/sell audit trail."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import OrderLog


def record_order(
    session: Session,
    *,
    user_id: str,
    symbol: str,
    side: str,
    qty: float,
    price: float | None = None,
    order_type: str | None = None,
    source: str | None = None,
    mode: str | None = None,
    broker_order_id: str | None = None,
) -> OrderLog:
    """Insert an order event and return it."""
    entry = OrderLog(
        user_id=user_id,
        symbol=symbol.upper(),
        side=side.lower(),
        qty=qty,
        price=price,
        order_type=order_type,
        source=source,
        mode=mode,
        broker_order_id=broker_order_id,
    )
    session.add(entry)
    return entry


def get_orders(session: Session, user_id: str, *, mode: str | None = None, limit: int = 50) -> list[OrderLog]:
    """Return recent orders, newest first."""
    q = select(OrderLog).where(OrderLog.user_id == user_id)
    if mode is not None:
        q = q.where(OrderLog.mode == mode)
    return list(session.scalars(q.order_by(OrderLog.created_at.desc()).limit(limit)))


def get_orders_for_symbol(
    session: Session, user_id: str, symbol: str, *, limit: int = 20
) -> list[OrderLog]:
    """Return recent orders for a specific symbol."""
    return list(
        session.scalars(
            select(OrderLog)
            .where(OrderLog.user_id == user_id, OrderLog.symbol == symbol.upper())
            .order_by(OrderLog.created_at.desc())
            .limit(limit)
        )
    )


def get_orders_for_date(
    session: Session, user_id: str, date, *, mode: str | None = None
) -> list[OrderLog]:
    """Return all orders for a specific date."""
    from datetime import timedelta, timezone
    day_start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    q = (
        select(OrderLog)
        .where(
            OrderLog.user_id == user_id,
            OrderLog.created_at >= day_start,
            OrderLog.created_at < day_end,
        )
    )
    if mode is not None:
        q = q.where(OrderLog.mode == mode)
    return list(session.scalars(q.order_by(OrderLog.created_at.asc())))
