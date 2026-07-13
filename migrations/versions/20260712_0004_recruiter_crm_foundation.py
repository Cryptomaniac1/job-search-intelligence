"""Add the recruiter CRM foundation."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0004"
down_revision: str | None = "20260712_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recruiters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("linkedin_url", sa.String(length=1000), nullable=False),
        sa.Column("phone", sa.String(length=100), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "recruiter_company_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recruiter_id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=300), nullable=False),
        sa.Column("normalized_company_name", sa.String(length=300), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recruiter_id",
            "normalized_company_name",
            name="uq_recruiter_company",
        ),
    )
    op.create_index(
        "ix_recruiter_company_normalized",
        "recruiter_company_links",
        ["normalized_company_name"],
    )
    op.create_table(
        "recruiter_email_addresses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recruiter_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=500), nullable=False),
        sa.Column("normalized_email", sa.String(length=500), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recruiter_id", "normalized_email", name="uq_recruiter_email"),
    )
    op.create_index(
        "ix_recruiter_email_normalized",
        "recruiter_email_addresses",
        ["normalized_email"],
    )
    op.create_table(
        "recruiter_job_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recruiter_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("source_message_identity", sa.String(length=67), nullable=False),
        sa.Column("relationship_type", sa.String(length=50), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.ForeignKeyConstraint(
            ["source_message_identity"],
            ["imported_messages.stable_message_identity"],
        ),
        sa.CheckConstraint(
            "relationship_type IN ('primary_recruiter', 'sourcer', 'coordinator', "
            "'hiring_contact', 'unknown')",
            name="ck_recruiter_job_relationship_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recruiter_id",
            "job_id",
            "relationship_type",
            name="uq_recruiter_job_relationship",
        ),
    )
    op.create_index("ix_recruiter_job_job_id", "recruiter_job_links", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_recruiter_job_job_id", table_name="recruiter_job_links")
    op.drop_table("recruiter_job_links")
    op.drop_index("ix_recruiter_email_normalized", table_name="recruiter_email_addresses")
    op.drop_table("recruiter_email_addresses")
    op.drop_index("ix_recruiter_company_normalized", table_name="recruiter_company_links")
    op.drop_table("recruiter_company_links")
    op.drop_table("recruiters")
