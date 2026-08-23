"""Add reviewed evidence links and reversible company aliases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0008"
down_revision: str | None = "20260808_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_job_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_identity", sa.String(length=67), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("link_method", sa.String(length=50), nullable=False, server_default="reviewed"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_identity"], ["imported_messages.stable_message_identity"]
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_identity", name="uq_evidence_job_links_message_identity"),
        sa.CheckConstraint("link_method = 'reviewed'", name="ck_evidence_job_links_method"),
    )
    op.create_index("ix_evidence_job_links_job_id", "evidence_job_links", ["job_id"])
    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alias_name", sa.String(length=300), nullable=False),
        sa.Column("normalized_alias", sa.String(length=300), nullable=False),
        sa.Column("canonical_name", sa.String(length=300), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias", name="uq_company_aliases_normalized_alias"),
    )


def downgrade() -> None:
    op.drop_table("company_aliases")
    op.drop_index("ix_evidence_job_links_job_id", table_name="evidence_job_links")
    op.drop_table("evidence_job_links")
