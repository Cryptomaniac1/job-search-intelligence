"""Add Yahoo IMAP checkpoints and immutable message transport metadata."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0006"
down_revision: str | None = "20260712_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imap_sync_checkpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("account_namespace", sa.String(length=500), nullable=False),
        sa.Column("folder", sa.String(length=1000), nullable=False),
        sa.Column("since_date", sa.Date(), nullable=False),
        sa.Column("uidvalidity", sa.String(length=100), nullable=False),
        sa.Column("last_successful_uid", sa.Integer(), nullable=False),
        sa.Column("sync_started_at", sa.DateTime(), nullable=False),
        sa.Column("sync_completed_at", sa.DateTime(), nullable=True),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("last_successful_uid >= 0", name="ck_imap_checkpoint_uid"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "account_namespace",
            "folder",
            "since_date",
            name="uq_imap_checkpoint_scope",
        ),
    )
    op.create_table(
        "imap_message_metadata",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_identity", sa.String(length=67), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("account_namespace", sa.String(length=500), nullable=False),
        sa.Column("folder", sa.String(length=1000), nullable=False),
        sa.Column("uidvalidity", sa.String(length=100), nullable=False),
        sa.Column("imap_uid", sa.Integer(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("imap_internal_date", sa.DateTime(), nullable=True),
        sa.Column("requested_since_date", sa.Date(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_fallback_used", sa.Boolean(), nullable=False),
        sa.Column("recipients_json", sa.Text(), nullable=False),
        sa.Column("attachments_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_identity"], ["imported_messages.stable_message_identity"]
        ),
        sa.CheckConstraint("imap_uid > 0", name="ck_imap_message_uid"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_identity", name="uq_imap_message_identity"),
        sa.UniqueConstraint(
            "provider",
            "account_namespace",
            "folder",
            "uidvalidity",
            "imap_uid",
            name="uq_imap_message_scope_uid",
        ),
    )
    op.create_index("ix_imap_message_scope", "imap_message_metadata", ["provider", "folder"])


def downgrade() -> None:
    op.drop_index("ix_imap_message_scope", table_name="imap_message_metadata")
    op.drop_table("imap_message_metadata")
    op.drop_table("imap_sync_checkpoints")
