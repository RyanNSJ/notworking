"""status_current.calm_runs for the detector's step-down rule (D19)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("status_current", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("calm_runs", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("status_current", schema=None) as batch_op:
        batch_op.drop_column("calm_runs")
