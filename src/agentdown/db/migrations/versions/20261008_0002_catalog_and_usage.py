"""catalogue-driven targets and usage counters

- targets.approved -> targets.listed (D55); drop columns now held by the catalogue file
- status columns widened for the new status names (D56)
- usage_daily for learning counters (D64)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("targets", schema=None) as batch_op:
        batch_op.alter_column("approved", new_column_name="listed")
        batch_op.drop_constraint("fk_targets_fronts_target_pk_targets", type_="foreignkey")
        batch_op.drop_column("fronts_target_pk")
        batch_op.drop_column("display_name")
        batch_op.drop_column("seed_source")

    with op.batch_alter_table("status_current", schema=None) as batch_op:
        batch_op.alter_column("status", type_=sa.String(32), existing_nullable=False)
    with op.batch_alter_table("status_transitions", schema=None) as batch_op:
        batch_op.alter_column("from_status", type_=sa.String(32), existing_nullable=False)
        batch_op.alter_column("to_status", type_=sa.String(32), existing_nullable=False)

    op.create_table(
        "usage_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("day", "event", "subject", name=op.f("pk_usage_daily")),
    )


def downgrade() -> None:
    op.drop_table("usage_daily")

    with op.batch_alter_table("status_transitions", schema=None) as batch_op:
        batch_op.alter_column("from_status", type_=sa.String(16), existing_nullable=False)
        batch_op.alter_column("to_status", type_=sa.String(16), existing_nullable=False)
    with op.batch_alter_table("status_current", schema=None) as batch_op:
        batch_op.alter_column("status", type_=sa.String(16), existing_nullable=False)

    with op.batch_alter_table("targets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("seed_source", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("display_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("fronts_target_pk", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_targets_fronts_target_pk_targets", "targets", ["fronts_target_pk"], ["pk"]
        )
        batch_op.alter_column("listed", new_column_name="approved")
