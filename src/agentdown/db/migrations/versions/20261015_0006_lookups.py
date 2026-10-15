"""lookups

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lookups",
        sa.Column("pk", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_id", sa.String(length=255), nullable=True),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("client", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("pk", name=op.f("pk_lookups")),
    )
    with op.batch_alter_table("lookups", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_lookups_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("lookups", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lookups_created_at"))
    op.drop_table("lookups")
