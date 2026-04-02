"""Add trading_rules table for rules engine.

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-04-01 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "8b9c0d1e2f3a"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trading_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rule_type", sa.String(16), nullable=False),
        sa.Column("rule_tree", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_rule_user", "trading_rules", ["user_id"])
    op.create_index("idx_rule_user_active", "trading_rules", ["user_id", "is_active"])


def downgrade() -> None:
    op.drop_index("idx_rule_user_active", table_name="trading_rules")
    op.drop_index("idx_rule_user", table_name="trading_rules")
    op.drop_table("trading_rules")
