"""add_account_runtime_tables

Revision ID: 3c2d4e5f6a7b
Revises: b7e2a1f4c890
Create Date: 2026-03-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c2d4e5f6a7b'
down_revision: Union[str, Sequence[str], None] = 'b7e2a1f4c890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'broker_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('provider_account_id', sa.String(length=128), nullable=True),
        sa.Column('paper', sa.Boolean(), nullable=False),
        sa.Column('credentials_ref', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_account_id', name='uq_broker_account_provider_ref'),
        sa.UniqueConstraint('user_id', name='uq_broker_account_user'),
    )
    op.create_index('idx_broker_account_active', 'broker_accounts', ['is_active'])

    op.create_table(
        'user_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(length=64), nullable=False),
        sa.Column('theme', sa.String(length=32), nullable=False),
        sa.Column('dashboard_layout', sa.Text(), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('notifications_enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_settings_user'),
    )

    op.create_table(
        'account_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('broker_account_id', sa.Integer(), nullable=False),
        sa.Column('trading_enabled', sa.Boolean(), nullable=False),
        sa.Column('strategy_slug', sa.String(length=64), nullable=False),
        sa.Column('risk_profile', sa.String(length=32), nullable=False),
        sa.Column('max_positions', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['broker_account_id'], ['broker_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('broker_account_id', name='uq_account_settings_account'),
    )


def downgrade() -> None:
    op.drop_table('account_settings')
    op.drop_table('user_settings')
    op.drop_index('idx_broker_account_active', table_name='broker_accounts')
    op.drop_table('broker_accounts')
