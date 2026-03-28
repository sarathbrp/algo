"""SQLAlchemy engine + session factory for AlgoSphere.

Configuration is read from environment variables:

- ``TIDB_DSN``        — full connection string, e.g.
  ``mysql+pymysql://user:pass@host:4000/algosphere?ssl_ca=/path/to/ca.pem``
- ``TIDB_SSL_CA``     — (optional) path to TiDB Serverless CA cert; appended to
  DSN as ``?ssl_ca=...`` when not already present in DSN.

When ``TIDB_DSN`` is not set the engine falls back to an in-memory SQLite
database — useful for tests and local development without a TiDB cluster.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _build_dsn() -> str:
    dsn = os.environ.get("TIDB_DSN", "")
    if dsn:
        ssl_ca = os.environ.get("TIDB_SSL_CA", "")
        if ssl_ca and "ssl_ca" not in dsn:
            sep = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{sep}ssl_ca={ssl_ca}"
        return dsn
    logger.warning("TIDB_DSN not set — using in-memory SQLite (development/test only)")
    return "sqlite://"


def _make_engine():
    dsn = _build_dsn()
    is_sqlite = dsn.startswith("sqlite")

    kwargs: dict = {
        "echo": os.environ.get("DB_ECHO", "").lower() in ("1", "true"),
        "future": True,
    }
    if not is_sqlite:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800

    eng = create_engine(dsn, **kwargs)

    # SQLite: enable WAL mode + foreign keys for local dev/test
    if is_sqlite:
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _record):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

    return eng


engine = _make_engine()
_SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a database session, committing on success or rolling back on error."""
    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
