"""Add trading history tables and mode columns.

- mode column on portfolio_snapshots and trades (paper/live)
- daily_summaries table (end-of-day stats)
- order_log table (buy/sell audit trail)
- position_snapshots table (position history)
- widen regime_log spy_score/qqq_score precision

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-03-30 17:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "7a8b9c0d1e2f"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add mode column to portfolio_snapshots
    with op.batch_alter_table("portfolio_snapshots") as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(length=8), nullable=True))

    # 2. Add mode column to trades
    with op.batch_alter_table("trades") as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(length=8), nullable=True))

    # 3. Widen regime_log score precision
    with op.batch_alter_table("regime_log") as batch_op:
        batch_op.alter_column(
            "spy_score",
            existing_type=sa.Numeric(6, 4),
            type_=sa.Numeric(10, 6),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "qqq_score",
            existing_type=sa.Numeric(6, 4),
            type_=sa.Numeric(10, 6),
            existing_nullable=True,
        )

    # 4. Create daily_summaries table
    op.create_table(
        "daily_summaries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("open_equity", sa.Numeric(18, 4), nullable=True),
        sa.Column("close_equity", sa.Numeric(18, 4), nullable=True),
        sa.Column("daily_pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("daily_pnl_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("daily_return_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("win_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("loss_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_trades_today", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_drawdown_pct", sa.Numeric(10, 6), nullable=True),
        sa.Column("positions_opened", sa.Integer, nullable=False, server_default="0"),
        sa.Column("positions_closed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    with op.batch_alter_table("daily_summaries") as batch_op:
        batch_op.create_index("idx_daily_summary_user_date", ["user_id", "date"])
        batch_op.create_unique_constraint("uq_daily_summary_user_date_mode", ["user_id", "date", "mode"])

    # 5. Create order_log table
    op.create_table(
        "order_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=True),
        sa.Column("order_type", sa.String(32), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("mode", sa.String(8), nullable=True),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    with op.batch_alter_table("order_log") as batch_op:
        batch_op.create_index("idx_order_log_user_time", ["user_id", "created_at"])
        batch_op.create_index("idx_order_log_user_symbol", ["user_id", "symbol"])

    # 6. Create position_snapshots table
    op.create_table(
        "position_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("current_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("mode", sa.String(8), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    with op.batch_alter_table("position_snapshots") as batch_op:
        batch_op.create_index("idx_pos_snapshot_user_time", ["user_id", "captured_at"])
        batch_op.create_index("idx_pos_snapshot_symbol", ["user_id", "symbol", "captured_at"])


def downgrade() -> None:
    op.drop_table("position_snapshots")
    op.drop_table("order_log")
    op.drop_table("daily_summaries")

    with op.batch_alter_table("regime_log") as batch_op:
        batch_op.alter_column(
            "spy_score",
            existing_type=sa.Numeric(10, 6),
            type_=sa.Numeric(6, 4),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "qqq_score",
            existing_type=sa.Numeric(10, 6),
            type_=sa.Numeric(6, 4),
            existing_nullable=True,
        )

    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_column("mode")

    with op.batch_alter_table("portfolio_snapshots") as batch_op:
        batch_op.drop_column("mode")
