"""Baseline the current jobs and email_imports schema."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("linkedin_job_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=False),
        sa.Column("salary_text", sa.String(length=300), nullable=False),
        sa.Column("applicant_count", sa.Integer(), nullable=True),
        sa.Column("applicant_count_is_over", sa.Boolean(), nullable=False),
        sa.Column("applicant_text", sa.String(length=300), nullable=False),
        sa.Column("easy_apply", sa.Boolean(), nullable=False),
        sa.Column("promoted", sa.Boolean(), nullable=False),
        sa.Column("posted_text", sa.String(length=200), nullable=False),
        sa.Column("work_mode", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("email_account", sa.Text(), server_default="", nullable=True),
        sa.Column("role_family", sa.Text(), server_default="", nullable=True),
        sa.Column("resume_family", sa.Text(), server_default="", nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("confirmation_message_id", sa.Text(), server_default="", nullable=True),
        sa.Column("ats_platform", sa.Text(), server_default="", nullable=True),
        sa.Column("requisition_id", sa.Text(), server_default="", nullable=True),
        sa.Column("application_source", sa.Text(), server_default="", nullable=True),
        sa.Column("import_confidence", sa.REAL(), server_default="0.0", nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_linkedin_job_id", "jobs", ["linkedin_job_id"], unique=True)
    op.create_table(
        "email_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mailbox_name", sa.String(length=50), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.Column("confirmations_found", sa.Integer(), nullable=False),
        sa.Column("matched_jobs", sa.Integer(), nullable=False),
        sa.Column("unmatched_jobs", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("email_imports")
    op.drop_index("ix_jobs_linkedin_job_id", table_name="jobs")
    op.drop_table("jobs")
