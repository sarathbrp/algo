"""Daily summary repository — end-of-day trading stats."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DailySummary


def record_daily_summary(
    session: Session,
    *,
    user_id: str,
    date: datetime,
    mode: str,
    open_equity: float | None = None,
    close_equity: float | None = None,
    daily_pnl: float | None = None,
    daily_pnl_pct: float | None = None,
    daily_return_pct: float | None = None,
    win_count: int = 0,
    loss_count: int = 0,
    total_trades_today: int = 0,
    max_drawdown_pct: float | None = None,
    positions_opened: int = 0,
    positions_closed: int = 0,
) -> DailySummary:
    """Insert or update a daily summary. Uses upsert logic on (user_id, date, mode)."""
    existing = session.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.date == date, DailySummary.mode == mode)
    )
    if existing is not None:
        existing.open_equity = open_equity
        existing.close_equity = close_equity
        existing.daily_pnl = daily_pnl
        existing.daily_pnl_pct = daily_pnl_pct
        existing.daily_return_pct = daily_return_pct
        existing.win_count = win_count
        existing.loss_count = loss_count
        existing.total_trades_today = total_trades_today
        existing.max_drawdown_pct = max_drawdown_pct
        existing.positions_opened = positions_opened
        existing.positions_closed = positions_closed
        return existing

    entry = DailySummary(
        user_id=user_id,
        date=date,
        mode=mode,
        open_equity=open_equity,
        close_equity=close_equity,
        daily_pnl=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        daily_return_pct=daily_return_pct,
        win_count=win_count,
        loss_count=loss_count,
        total_trades_today=total_trades_today,
        max_drawdown_pct=max_drawdown_pct,
        positions_opened=positions_opened,
        positions_closed=positions_closed,
    )
    session.add(entry)
    return entry


def get_daily_summaries(
    session: Session, user_id: str, *, mode: str | None = None, limit: int = 30
) -> list[DailySummary]:
    """Return recent daily summaries, newest first."""
    q = select(DailySummary).where(DailySummary.user_id == user_id)
    if mode is not None:
        q = q.where(DailySummary.mode == mode)
    return list(session.scalars(q.order_by(DailySummary.date.desc()).limit(limit)))


def get_summary_for_date(
    session: Session, user_id: str, date, mode: str
) -> DailySummary | None:
    """Return the summary for a specific date and mode."""
    return session.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.date == date, DailySummary.mode == mode)
    )
