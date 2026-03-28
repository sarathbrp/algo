import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make src/ importable when running alembic from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Prefer TIDB_DSN env var over alembic.ini sqlalchemy.url."""
    dsn = os.environ.get("TIDB_DSN", "")
    if dsn:
        ssl_ca = os.environ.get("TIDB_SSL_CA", "")
        if ssl_ca and "ssl_ca" not in dsn:
            sep = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{sep}ssl_ca={ssl_ca}"
        return dsn
    return config.get_main_option("sqlalchemy.url") or "sqlite://"


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
