"""lookups owner and signature agent

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("lookups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("signature_agent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lookups", schema=None) as batch_op:
        batch_op.drop_column("signature_agent")
        batch_op.drop_column("owner")
