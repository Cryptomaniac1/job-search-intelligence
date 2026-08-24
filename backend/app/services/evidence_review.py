"""Reviewer-facing outcome evidence queue without automatic job matching."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

REVIEWABLE_CLASSIFICATIONS = (
    "RECRUITER_OUTREACH",
    "RECRUITER_REPLY",
    "RECRUITER_FOLLOW_UP",
    "INTERVIEW_INVITATION",
    "INTERVIEW_CONFIRMATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION",
    "ASSESSMENT_INVITATION",
    "ASSESSMENT_REMINDER",
    "OFFER",
    "OFFER_UPDATE",
    "OFFER_EXPIRED",
    "OFFER_ACCEPTED",
    "OFFER_DECLINED",
    "REJECTION",
    "POSITION_CLOSED",
)


def list_unlinked_evidence(
    database_path: Path, *, limit: int = 100, actionable_only: bool = False
) -> dict[str, Any]:
    """Return review work items without inferring or persisting a proposed link.

    ``actionable_only`` limits the queue to messages with retained sender or subject metadata.
    The dashboard uses it so a reviewer never has to guess from a timestamp alone. Message
    bodies remain out of the API response.
    """
    placeholders = ", ".join("?" for _ in REVIEWABLE_CLASSIFICATIONS)
    link_join = "LEFT JOIN evidence_job_links AS l ON l.message_identity = c.message_identity"
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    if "evidence_job_links" not in tables:
        link_join = ""
        reviewed_filter = ""
    else:
        reviewed_filter = "AND l.message_identity IS NULL"
    context_expression = "(COALESCE(m.subject, '') <> '' OR COALESCE(m.sender, '') <> '')"
    actionable_filter = f"AND {context_expression}" if actionable_only else ""
    query = f"""
        SELECT c.message_identity, c.classification, c.confidence, c.classifier_version,
               c.reason_json,
               COALESCE(m.provider, i.provider, '') AS provider,
               COALESCE(m.account_namespace, e.mailbox_name, '') AS account_namespace,
               COALESCE(
                   m.imap_internal_date, m.received_at, i.imported_at, c.created_at
               ) AS occurred_at,
               COALESCE(m.subject, '') AS subject,
               COALESCE(m.sender, '') AS sender
          FROM email_classifications AS c
          LEFT JOIN imported_messages AS i ON i.stable_message_identity = c.message_identity
          LEFT JOIN email_imports AS e ON e.id = i.source_import_id
          LEFT JOIN imap_message_metadata AS m ON m.message_identity = c.message_identity
          {link_join}
         WHERE c.job_id IS NULL AND c.classification IN ({placeholders}) {reviewed_filter}
               {actionable_filter}
         ORDER BY occurred_at DESC, c.message_identity
         LIMIT ?
    """
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, (*REVIEWABLE_CLASSIFICATIONS, limit)).fetchall()
        total = connection.execute(
            f"SELECT COUNT(*) FROM email_classifications "
            f"WHERE job_id IS NULL AND classification IN ({placeholders})",
            REVIEWABLE_CLASSIFICATIONS,
        ).fetchone()[0]
        if "evidence_job_links" in tables:
            total -= connection.execute(
                "SELECT COUNT(*) FROM evidence_job_links AS l "
                "JOIN email_classifications AS c ON c.message_identity = l.message_identity "
                f"WHERE c.job_id IS NULL AND c.classification IN ({placeholders})",
                REVIEWABLE_CLASSIFICATIONS,
            ).fetchone()[0]
        context_available = connection.execute(
            f"SELECT COUNT(*) FROM email_classifications AS c "
            "LEFT JOIN imap_message_metadata AS m ON m.message_identity = c.message_identity "
            f"WHERE c.job_id IS NULL AND c.classification IN ({placeholders}) "
            f"AND {context_expression}",
            REVIEWABLE_CLASSIFICATIONS,
        ).fetchone()[0]
        if "evidence_job_links" in tables:
            context_available -= connection.execute(
                "SELECT COUNT(*) FROM evidence_job_links AS l "
                "JOIN email_classifications AS c ON c.message_identity = l.message_identity "
                "LEFT JOIN imap_message_metadata AS m ON m.message_identity = c.message_identity "
                f"WHERE c.job_id IS NULL AND c.classification IN ({placeholders}) "
                f"AND {context_expression}",
                REVIEWABLE_CLASSIFICATIONS,
            ).fetchone()[0]
    return {
        "total_unlinked": total,
        "context_available": context_available,
        "context_unavailable": total - context_available,
        "actionable_only": actionable_only,
        "returned": len(rows),
        "items": [_review_item(row) for row in rows],
    }


def create_reviewed_job_link(
    database_path: Path, *, message_identity: str, job_id: int, reason: str
) -> dict[str, Any]:
    """Persist a human-reviewed link without changing immutable email evidence."""
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("A review reason is required.")
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(database_path) as connection:
        if not connection.execute(
            "SELECT 1 FROM email_classifications WHERE message_identity = ?", (message_identity,)
        ).fetchone():
            raise LookupError("Evidence record was not found.")
        if not connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
            raise LookupError("Job was not found.")
        connection.execute(
            """INSERT INTO evidence_job_links
               (message_identity, job_id, link_method, reason, created_at, updated_at)
               VALUES (?, ?, 'reviewed', ?, ?, ?)
               ON CONFLICT(message_identity) DO UPDATE SET
                   job_id = excluded.job_id,
                   reason = excluded.reason,
                   updated_at = excluded.updated_at""",
            (message_identity, job_id, clean_reason, now, now),
        )
    return {"message_identity": message_identity, "job_id": job_id, "link_method": "reviewed"}


def create_company_alias(
    database_path: Path, *, alias_name: str, canonical_name: str, reason: str
) -> dict[str, Any]:
    """Save a reversible display alias; raw company values remain unchanged."""
    alias = alias_name.strip()
    canonical = canonical_name.strip()
    clean_reason = reason.strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", alias.casefold()).strip()
    if not alias or not canonical or not clean_reason or not normalized:
        raise ValueError("Alias, canonical company, and review reason are required.")
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO company_aliases
               (alias_name, normalized_alias, canonical_name, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(normalized_alias) DO UPDATE SET
                   alias_name = excluded.alias_name, canonical_name = excluded.canonical_name,
                   reason = excluded.reason, updated_at = excluded.updated_at""",
            (alias, normalized, canonical, clean_reason, now, now),
        )
    return {"alias_name": alias, "canonical_name": canonical, "normalized_alias": normalized}


def _review_item(row: sqlite3.Row) -> dict[str, Any]:
    """Expose local review metadata and rule names; never return a raw message body."""
    try:
        reasons = json.loads(str(row["reason_json"] or "{}"))
    except json.JSONDecodeError:
        reasons = {}
    if not isinstance(reasons, dict):
        reasons = {}
    signals = reasons.get("matched_signals") or reasons.get("reasons") or []
    if not isinstance(signals, list):
        signals = []
    return {
        "message_identity": row["message_identity"],
        "classification": row["classification"],
        "confidence": row["confidence"],
        "classifier_version": row["classifier_version"],
        "provider": row["provider"],
        "account_namespace": row["account_namespace"],
        "occurred_at": row["occurred_at"],
        "subject": row["subject"],
        "sender": row["sender"],
        "matched_signals": [str(signal) for signal in signals[:10]],
    }
