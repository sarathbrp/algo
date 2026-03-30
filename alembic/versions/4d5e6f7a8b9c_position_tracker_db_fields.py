"""position_tracker_db_fields

Revision ID: 4d5e6f7a8b9c
Revises: 3c2d4e5f6a7b
Create Date: 2026-03-28 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d5e6f7a8b9c'
down_revision: Union[str, Sequence[str], None] = '3c2d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('positions') as batch_op:
        batch_op.add_column(sa.Column('last_buy_price', sa.Numeric(18, 4), nullable=True))
        batch_op.create_unique_constraint('uq_position_user_symbol', ['user_id', 'symbol'])


def downgrade() -> None:
    with op.batch_alter_table('positions') as batch_op:
        batch_op.drop_constraint('uq_position_user_symbol', type_='unique')
        batch_op.drop_column('last_buy_price')
