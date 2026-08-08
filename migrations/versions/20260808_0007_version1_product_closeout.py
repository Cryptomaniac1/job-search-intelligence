"""Add Version 1 daily-use domain records without rewriting legacy jobs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0007"
down_revision: str | None = "20260712_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("normalized_name", sa.String(length=300), nullable=False),
        sa.Column("website", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("industry", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_companies_normalized_name"),
    )
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("family", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("industries_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_resumes_name_version"),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("resume_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="applied"),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("match_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_applications_job_id"),
        sa.CheckConstraint(
            "match_score >= 0 AND match_score <= 100", name="ck_application_match_score"
        ),
        sa.CheckConstraint(
            "status IN ('applied','recruiter','interview','offer','rejected','withdrawn')",
            name="ck_application_status",
        ),
    )
    op.create_table(
        "job_descriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("source_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requirements_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("skills_json", sa.Text(), nullable=False, server_default="[]"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "source_hash", name="uq_job_description_source"),
        sa.CheckConstraint(
            "source_type IN ('text','html','pdf')", name="ck_job_description_source_type"
        ),
    )
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="received"),
        sa.Column("base_salary", sa.Float(), nullable=True),
        sa.Column("bonus", sa.Float(), nullable=True),
        sa.Column("equity", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("offered_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_offers_application_id"),
        sa.CheckConstraint(
            "status IN ('received','negotiating','accepted','declined','expired')",
            name="ck_offer_status",
        ),
    )
    op.create_table(
        "recruiter_relationships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recruiter_id", sa.Integer(), nullable=False),
        sa.Column(
            "relationship_status", sa.String(length=50), nullable=False, server_default="active"
        ),
        sa.Column("last_contact_at", sa.DateTime(), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(), nullable=True),
        sa.Column("response_latency_hours", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recruiter_id", name="uq_recruiter_relationship_recruiter"),
        sa.CheckConstraint(
            "relationship_status IN ('active','warm','dormant','closed')",
            name="ck_recruiter_relationship_status",
        ),
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("application_id", sa.Integer(), nullable=True),
        sa.Column("recruiter_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("interview_id", sa.Integer(), nullable=True),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("source_message_identity", sa.String(length=67), nullable=True),
        sa.Column("interaction_type", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("immutable_evidence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["interview_id"], ["interviews.id"]),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"]),
        sa.ForeignKeyConstraint(
            ["source_message_identity"], ["imported_messages.stable_message_identity"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for table, column in (
        ("applications", "status"),
        ("applications", "company_id"),
        ("offers", "status"),
        ("interactions", "company_id"),
        ("interactions", "occurred_at"),
        ("notes", "entity_type"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "interactions",
        "notes",
        "recruiter_relationships",
        "offers",
        "job_descriptions",
        "applications",
        "resumes",
        "companies",
    ):
        op.drop_table(table)
