"""Add deterministic interview aggregates and immutable event evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0005"
down_revision: str | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("recruiter_id", sa.Integer(), nullable=True),
        sa.Column("interview_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=True),
        sa.Column("location_type", sa.String(length=50), nullable=True),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("meeting_url", sa.String(length=2000), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("first_source_message_identity", sa.String(length=67), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.ForeignKeyConstraint(
            ["first_source_message_identity"],
            ["imported_messages.stable_message_identity"],
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','confirmed','rescheduled','cancelled')",
            name="ck_interviews_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interviews_job_id", "interviews", ["job_id"])
    op.create_index("ix_interviews_scheduled_start", "interviews", ["scheduled_start"])
    op.create_index("ix_interviews_status", "interviews", ["status"])
    op.create_table(
        "interview_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("interview_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("recruiter_id", sa.Integer(), nullable=True),
        sa.Column("source_message_identity", sa.String(length=67), nullable=False),
        sa.Column("classification_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("extracted_start", sa.DateTime(), nullable=True),
        sa.Column("extracted_end", sa.DateTime(), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=True),
        sa.Column("location_type", sa.String(length=50), nullable=True),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("meeting_url", sa.String(length=2000), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["classification_id"], ["email_classifications.id"]),
        sa.ForeignKeyConstraint(["interview_id"], ["interviews.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["recruiter_id"], ["recruiters.id"]),
        sa.ForeignKeyConstraint(
            ["source_message_identity"], ["imported_messages.stable_message_identity"]
        ),
        sa.CheckConstraint(
            "event_type IN ('invitation','confirmation','reschedule','cancellation',"
            "'assessment_invitation','assessment_reminder')",
            name="ck_interview_events_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_message_identity",
            "extractor_version",
            name="uq_interview_event_source_version",
        ),
    )
    op.create_index("ix_interview_events_interview_id", "interview_events", ["interview_id"])
    op.create_index("ix_interview_events_job_id", "interview_events", ["job_id"])
    op.create_index("ix_interview_events_event_type", "interview_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_interview_events_event_type", table_name="interview_events")
    op.drop_index("ix_interview_events_job_id", table_name="interview_events")
    op.drop_index("ix_interview_events_interview_id", table_name="interview_events")
    op.drop_table("interview_events")
    op.drop_index("ix_interviews_status", table_name="interviews")
    op.drop_index("ix_interviews_scheduled_start", table_name="interviews")
    op.drop_index("ix_interviews_job_id", table_name="interviews")
    op.drop_table("interviews")
