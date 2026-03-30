"""Worker health persistence helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from src.db import get_session
from src.db.repos import worker_repo


def record_heartbeat(
    worker_name: str,
    *,
    status: str,
    current_user_id: str | None = None,
    last_error: str | None = None,
    last_reconciled_at: datetime | None = None,
) -> None:
    with get_session() as session:
        worker_repo.upsert_worker_status(
            session,
            worker_name=worker_name,
            status=status,
            current_user_id=current_user_id,
            last_error=last_error,
            last_reconciled_at=last_reconciled_at,
        )


def mark_starting(worker_name: str) -> None:
    record_heartbeat(worker_name, status="starting")


def mark_running(worker_name: str, *, current_user_id: str | None = None, last_reconciled_at: datetime | None = None) -> None:
    record_heartbeat(
        worker_name,
        status="running",
        current_user_id=current_user_id,
        last_reconciled_at=last_reconciled_at,
    )


def mark_error(worker_name: str, error: str, *, current_user_id: str | None = None) -> None:
    record_heartbeat(
        worker_name,
        status="error",
        current_user_id=current_user_id,
        last_error=error,
    )


def mark_stopped(worker_name: str) -> None:
    record_heartbeat(worker_name, status="stopped")
