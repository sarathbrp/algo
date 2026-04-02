"""Shared broker construction utility for API routers."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from src.db.repos import account_repo, user_repo

_logger = logging.getLogger(__name__)


def get_live_broker(session: Session, user_id: str) -> Any | None:
    """Build a temporary AlpacaBroker from stored user credentials.

    Returns None if the user has no credentials or broker init fails.
    """
    try:
        from src.brokers.alpaca_client import AlpacaBroker

        user = user_repo.get_by_id(session, user_id)
        if user is None:
            return None
        key = getattr(user, "alpaca_key_env", None)
        secret = getattr(user, "alpaca_secret_env", None)
        if not key or not secret:
            return None
        broker_account = account_repo.get_broker_account_for_user(session, user_id)
        paper = bool(broker_account.paper) if broker_account else True
        return AlpacaBroker(api_key=key, secret=secret, paper=paper)
    except Exception as exc:
        _logger.debug("Live broker init failed for %s: %s", user_id, exc)
        return None
