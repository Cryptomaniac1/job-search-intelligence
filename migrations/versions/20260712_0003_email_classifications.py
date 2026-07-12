"""Add deterministic email classification evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0003"
down_revision: str | None = "20260712_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_classifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_identity", sa.String(length=67), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("classifier_version", sa.String(length=50), nullable=False),
        sa.Column("reason_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_identity"],
            ["imported_messages.stable_message_identity"],
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_identity",
            "classifier_version",
            name="uq_email_classification_version",
        ),
    )
    op.create_index("ix_email_classifications_type", "email_classifications", ["classification"])
    op.create_index("ix_email_classifications_job_id", "email_classifications", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_email_classifications_job_id", table_name="email_classifications")
    op.drop_index("ix_email_classifications_type", table_name="email_classifications")
    op.drop_table("email_classifications")
