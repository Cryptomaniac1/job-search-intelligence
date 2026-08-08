"""Read-only provider synchronization status without credential or account disclosure."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

PROVIDERS = ("gmail", "hotmail", "yahoo")


def _account_reference(value: str) -> str:
    return hashlib.sha256(value.casefold().strip().encode()).hexdigest()[:12]


def provider_sync_status(database: Path) -> dict[str, Any]:
    """Summarize provider evidence and checkpoints through a query-only connection."""
    resolved = database.expanduser().resolve()
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        metadata_counts = dict(
            connection.execute(
                "SELECT provider,COUNT(*) FROM imap_message_metadata GROUP BY provider"
            )
        )
        classification_counts = dict(
            connection.execute(
                "SELECT m.provider,COUNT(*) FROM imap_message_metadata m "
                "JOIN email_classifications c ON c.message_identity=m.message_identity "
                "GROUP BY m.provider"
            )
        )
        interview_counts = dict(
            connection.execute("SELECT provider,COUNT(*) FROM interview_events GROUP BY provider")
        )
        rows = list(
            connection.execute(
                "SELECT provider,account_namespace,folder,since_date,uidvalidity,"
                "last_successful_uid,sync_started_at,sync_completed_at,scanned_count,"
                "accepted_count,skipped_count,failure_count "
                "FROM imap_sync_checkpoints ORDER BY provider,folder,since_date"
            )
        )
    scopes: dict[str, list[dict[str, Any]]] = {provider: [] for provider in PROVIDERS}
    for row in rows:
        provider = str(row["provider"])
        if provider not in scopes:
            continue
        scopes[provider].append(
            {
                "account_reference": _account_reference(str(row["account_namespace"])),
                "folder": str(row["folder"]),
                "since_date": str(row["since_date"]),
                "uidvalidity": str(row["uidvalidity"]),
                "last_successful_uid": int(row["last_successful_uid"]),
                "sync_started_at": str(row["sync_started_at"]),
                "sync_completed_at": (
                    str(row["sync_completed_at"]) if row["sync_completed_at"] else None
                ),
                "scanned_count": int(row["scanned_count"]),
                "accepted_count": int(row["accepted_count"]),
                "skipped_count": int(row["skipped_count"]),
                "failure_count": int(row["failure_count"]),
            }
        )
    providers = []
    for provider in PROVIDERS:
        evidence_count = int(metadata_counts.get(provider, 0))
        checkpoint_count = len(scopes[provider])
        state = (
            "checkpointed"
            if checkpoint_count
            else ("evidence_without_checkpoint" if evidence_count else "never_synced")
        )
        providers.append(
            {
                "provider": provider,
                "state": state,
                "message_evidence_count": evidence_count,
                "classification_count": int(classification_counts.get(provider, 0)),
                "interview_event_count": int(interview_counts.get(provider, 0)),
                "checkpoint_count": checkpoint_count,
                "scopes": scopes[provider],
            }
        )
    return {"providers": providers, "credentials_exposed": False, "database_writes": 0}
