"""index targets.target_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("targets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_targets_target_id"), ["target_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("targets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_targets_target_id"))
