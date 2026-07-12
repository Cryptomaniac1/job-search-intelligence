"""Add immutable imported-message identity and provenance."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0002"
down_revision: str | None = "20260712_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imported_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_import_id", sa.Integer(), nullable=False),
        sa.Column("stable_message_identity", sa.String(length=67), nullable=False),
        sa.Column("original_message_id", sa.String(length=500), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["source_import_id"], ["email_imports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_message_identity", name="uq_imported_message_identity"),
    )
    op.create_index("ix_imported_messages_provider", "imported_messages", ["provider"])
    op.create_index("ix_imported_messages_job_id", "imported_messages", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_imported_messages_job_id", table_name="imported_messages")
    op.drop_index("ix_imported_messages_provider", table_name="imported_messages")
    op.drop_table("imported_messages")
