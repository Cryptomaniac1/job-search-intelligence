"""Read-only review queue for outcome evidence without a deterministic job link."""

from __future__ import annotations

import json
import sqlite3
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


def list_unlinked_evidence(database_path: Path, *, limit: int = 100) -> dict[str, Any]:
    """Return provenance-only work items; never infer or persist a proposed link."""
    placeholders = ", ".join("?" for _ in REVIEWABLE_CLASSIFICATIONS)
    query = f"""
        SELECT c.message_identity, c.classification, c.confidence, c.classifier_version,
               c.reason_json,
               COALESCE(m.provider, i.provider, '') AS provider,
               COALESCE(m.account_namespace, e.mailbox_name, '') AS account_namespace,
               COALESCE(
                   m.imap_internal_date, m.received_at, i.imported_at, c.created_at
               ) AS occurred_at
          FROM email_classifications AS c
          LEFT JOIN imported_messages AS i ON i.stable_message_identity = c.message_identity
          LEFT JOIN email_imports AS e ON e.id = i.source_import_id
          LEFT JOIN imap_message_metadata AS m ON m.message_identity = c.message_identity
         WHERE c.job_id IS NULL AND c.classification IN ({placeholders})
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
    return {
        "total_unlinked": total,
        "returned": len(rows),
        "items": [_review_item(row) for row in rows],
    }


def _review_item(row: sqlite3.Row) -> dict[str, Any]:
    """Expose rule names only; raw subjects, senders, and bodies stay in immutable storage."""
    try:
        reasons = json.loads(str(row["reason_json"] or "{}"))
    except json.JSONDecodeError:
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
        "matched_signals": [str(signal) for signal in signals[:10]],
    }
