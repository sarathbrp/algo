"""User context for the MCP server.

The MCP server runs locally and connects to the DB directly — no JWT needed.
User ID is set via ALGOSPHERE_USER_ID environment variable.
"""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from src.db.connection import _SessionLocal
from src.db.repos import user_repo


def get_user_id() -> str:
    """Get the configured user ID from environment."""
    uid = os.environ.get("ALGOSPHERE_USER_ID", "")
    if not uid:
        raise RuntimeError(
            "ALGOSPHERE_USER_ID environment variable not set. "
            "Set it to your AlgoSphere user ID."
        )
    return uid


def get_session() -> Session:
    """Create a new DB session."""
    return _SessionLocal()


def verify_user(session: Session, user_id: str) -> bool:
    """Verify the user exists in the DB."""
    user = user_repo.get_by_id(session, user_id)
    return user is not None
