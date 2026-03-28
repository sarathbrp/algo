"""add_risk_profile

Revision ID: b7e2a1f4c890
Revises: dae1c633f2ac
Create Date: 2026-03-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2a1f4c890'
down_revision: Union[str, Sequence[str], None] = 'dae1c633f2ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('risk_profile', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'risk_profile')
