"""Add bot_state to account_settings.

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
Create Date: 2026-03-28 18:55:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6f7a8b9c0d1e"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_settings",
        sa.Column(
            "bot_state",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
    )
    op.execute(
        "UPDATE account_settings SET bot_state = CASE "
        "WHEN trading_enabled THEN 'running' ELSE 'paused' END"
    )
    op.alter_column("account_settings", "bot_state", server_default=None)


def downgrade() -> None:
    op.drop_column("account_settings", "bot_state")
