"""Add symbol column to trading_rules.

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-04-02 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9c0d1e2f3a4b"
down_revision = "8b9c0d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trading_rules", sa.Column("symbol", sa.String(16), nullable=True))
    # Backfill any existing rules with empty symbol
    op.execute("UPDATE trading_rules SET symbol = '' WHERE symbol IS NULL")
    op.alter_column("trading_rules", "symbol", nullable=False, existing_type=sa.String(16))
    op.create_index("idx_rule_user_symbol", "trading_rules", ["user_id", "symbol"])


def downgrade() -> None:
    op.drop_index("idx_rule_user_symbol", table_name="trading_rules")
    op.drop_column("trading_rules", "symbol")
