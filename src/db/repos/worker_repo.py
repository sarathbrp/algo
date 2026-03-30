"""Worker status repository."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import WorkerStatus


def upsert_worker_status(
    session: Session,
    *,
    worker_name: str,
    status: str,
    current_user_id: str | None = None,
    last_error: str | None = None,
    last_reconciled_at: datetime | None = None,
) -> WorkerStatus:
    row = session.scalar(
        select(WorkerStatus).where(WorkerStatus.worker_name == worker_name)
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = WorkerStatus(
            worker_name=worker_name,
            status=status,
            current_user_id=current_user_id,
            last_error=last_error,
            last_heartbeat=now,
            last_reconciled_at=last_reconciled_at,
        )
        session.add(row)
        return row

    row.status = status
    row.current_user_id = current_user_id
    row.last_error = last_error
    row.last_heartbeat = now
    if last_reconciled_at is not None:
        row.last_reconciled_at = last_reconciled_at
    return row


def get_worker_status(session: Session, worker_name: str) -> WorkerStatus | None:
    return session.scalar(
        select(WorkerStatus).where(WorkerStatus.worker_name == worker_name)
    )
