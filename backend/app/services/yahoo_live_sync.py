"""Offline approval gate and database evidence for Yahoo production synchronization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from backend.app.services.imap_checkpoint import EXPECTED_REVISION, ImapCheckpoint
from backend.app.services.yahoo_imap import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    YahooImapSettings,
    create_verified_tls_context,
)

EXPECTED_LIVE_DATABASE = (Path(__file__).resolve().parents[3] / "data" / "jobs.db").resolve()
EXPECTED_LIVE_CHECKSUM = "088e96d7d518815ef5b1de757a6e7d6aaff9695b9d4706f8d25602952c4a91b0"
EXPECTED_FOLDER = "job"
EXPECTED_SINCE_DATE = date(2024, 7, 1)
FIRST_LIVE_UID = 53290
FIRST_LIVE_LIMIT = 100
LIVE_CONFIRMATION_TOKEN = "YAHOO-LIVE-SYNC"

EVIDENCE_TABLES = (
    "jobs",
    "email_imports",
    "imported_messages",
    "email_classifications",
    "recruiters",
    "recruiter_company_links",
    "recruiter_email_addresses",
    "recruiter_job_links",
    "interviews",
    "interview_events",
    "imap_sync_checkpoints",
    "imap_message_metadata",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _validate_database(path: Path, expected_checksum: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Live database does not exist: {resolved}")
    checksum = sha256_file(resolved)
    if checksum != expected_checksum:
        raise ValueError("Live database checksum does not match the approved post-migration value")
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    if revision != (EXPECTED_REVISION,):
        raise ValueError(f"Live database must be at Alembic revision {EXPECTED_REVISION}")
    if integrity != ["ok"] or foreign_keys:
        raise ValueError("Live database integrity or foreign-key validation failed")
    return {"checksum_sha256": checksum, "revision": revision[0], "integrity_check": integrity}


def _validate_backup(metadata_path: Path) -> dict[str, Any]:
    metadata = _read_json(metadata_path, "Backup metadata")
    backup = Path(str(metadata.get("path", ""))).expanduser().resolve()
    if not backup.is_file():
        raise ValueError("Backup metadata does not reference a readable backup")
    checksum = sha256_file(backup)
    if checksum != metadata.get("checksum_sha256"):
        raise ValueError("Backup checksum does not match its metadata")
    if metadata.get("alembic_revision") != "20260712_0005":
        raise ValueError("Approved pre-sync backup must be at revision 20260712_0005")
    if metadata.get("integrity_check") != ["ok"] or metadata.get("foreign_key_violations"):
        raise ValueError("Backup metadata does not show a clean database")
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Backup is not readable and integral")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if revision != ("20260712_0005",):
            raise ValueError("Backup database is not at revision 20260712_0005")
    return {"path": str(backup), "checksum_sha256": checksum, "revision": "20260712_0005"}


def _validate_dry_run(path: Path) -> dict[str, Any]:
    evidence = _read_json(path, "Dry-run evidence")
    expected = {
        "folder": "job",
        "since_date": "2024-07-01",
        "requested_start_uid": 53290,
        "search_complete": True,
        "total_matched_uid_count": 1000,
        "batch_selected_count": 100,
        "processed_count": 100,
        "completed_count": 100,
        "accepted_candidates": 100,
        "failure_count": 0,
        "database_writes": 0,
        "mailbox_mutations": 0,
        "uidvalidity": "1578947209",
    }
    mismatches = [key for key, value in expected.items() if evidence.get(key) != value]
    if mismatches:
        raise ValueError(f"Dry-run evidence failed approved fields: {', '.join(mismatches)}")
    return {key: evidence[key] for key in expected}


def _validate_offline_tls(settings: YahooImapSettings) -> dict[str, Any]:
    if settings.host != DEFAULT_HOST or settings.port != DEFAULT_PORT:
        raise ValueError("Live Yahoo sync requires imap.mail.yahoo.com with TLS on port 993")
    if not settings.username or not settings.app_password:
        raise ValueError("Yahoo IMAP credentials are required")
    context = create_verified_tls_context()
    if not context.get_ca_certs():
        raise ValueError("No trusted TLS certificate authorities are available")
    return {"host": settings.host, "port": settings.port, "certificate_verification": True}


def preflight_live_sync(
    database: Path,
    *,
    folder: str,
    since_date: date,
    backup_metadata: Path,
    dry_run_evidence: Path,
    settings: YahooImapSettings,
    expected_live_path: Path = EXPECTED_LIVE_DATABASE,
    expected_checksum: str = EXPECTED_LIVE_CHECKSUM,
) -> dict[str, Any]:
    """Validate every live prerequisite without connecting or writing."""
    resolved = database.expanduser().resolve()
    if resolved != expected_live_path.expanduser().resolve():
        raise ValueError(f"Live database path must resolve exactly to {expected_live_path}")
    if folder != EXPECTED_FOLDER:
        raise ValueError("First live synchronization requires the exact folder 'job'")
    if since_date != EXPECTED_SINCE_DATE:
        raise ValueError("First live synchronization requires --since-date 2024-07-01")
    return {
        "mode": "preflight-live",
        "database": str(resolved),
        "folder": folder,
        "since_date": since_date.isoformat(),
        "database_evidence": _validate_database(resolved, expected_checksum),
        "backup_evidence": _validate_backup(backup_metadata),
        "dry_run_evidence": _validate_dry_run(dry_run_evidence),
        "tls": _validate_offline_tls(settings),
        "network_connections": 0,
        "database_writes": 0,
        "mailbox_mutations": 0,
    }


def authorize_first_live_batch(
    *, allow_live: bool, confirmation: str | None, start_uid: int, limit: int | None
) -> None:
    if not allow_live:
        raise ValueError("Live synchronization requires --allow-live-database")
    if confirmation != LIVE_CONFIRMATION_TOKEN:
        raise ValueError("Live synchronization confirmation token is invalid")
    if start_uid != FIRST_LIVE_UID or limit != FIRST_LIVE_LIMIT:
        raise ValueError("First live batch requires --start-uid 53290 --limit 100")


def database_state(path: Path) -> dict[str, Any]:
    """Capture non-secret synchronization evidence from a database."""
    resolved = path.resolve()
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in EVIDENCE_TABLES
        }
        classifications = dict(
            connection.execute(
                "SELECT classification, COUNT(*) FROM email_classifications GROUP BY classification"
            )
        )
        unresolved = int(
            connection.execute(
                "SELECT COUNT(*) FROM imported_messages WHERE job_id IS NULL"
            ).fetchone()[0]
        )
        revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "checksum_sha256": sha256_file(resolved),
        "revision": revision,
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
        "row_counts": counts,
        "classification_counts": classifications,
        "unresolved_evidence_count": unresolved,
    }


def state_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    return {
        table: int(after["row_counts"][table]) - int(before["row_counts"][table])
        for table in EVIDENCE_TABLES
    }


def checkpoint_evidence(checkpoint: ImapCheckpoint | None) -> dict[str, Any] | None:
    return asdict(checkpoint) if checkpoint else None


def idempotency_evidence(path: Path) -> dict[str, Any]:
    """Confirm persisted uniqueness invariants after a production batch."""
    checks = {
        "imported_messages": "stable_message_identity",
        "imap_message_metadata": "message_identity",
        "email_classifications": "message_identity || '|' || classifier_version",
        "recruiter_email_addresses": "recruiter_id || '|' || normalized_email",
        "recruiter_company_links": "recruiter_id || '|' || normalized_company_name",
        "recruiter_job_links": "recruiter_id || '|' || job_id || '|' || relationship_type",
        "interview_events": "source_message_identity || '|' || extractor_version",
    }
    duplicates: dict[str, int] = {}
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        for table, expression in checks.items():
            query = (
                f'SELECT COUNT(*) FROM (SELECT {expression} AS identity FROM "{table}" '
                "GROUP BY identity HAVING COUNT(*) > 1)"
            )
            duplicates[table] = int(connection.execute(query).fetchone()[0])
    return {"duplicate_groups": duplicates, "passed": not any(duplicates.values())}


def idempotency_token(
    *,
    account: str,
    folder: str,
    since_date: date,
    uidvalidity: str,
    first_uid: int | None,
    last_uid: int | None,
) -> str:
    scope = (
        f"yahoo|{account}|{folder}|{since_date.isoformat()}|{uidvalidity}|{first_uid}|{last_uid}"
    )
    return hashlib.sha256(scope.encode()).hexdigest()
