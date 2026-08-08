"""Read-only Yahoo import verification and incident recovery analysis."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .email_classification import CLASSIFIER_VERSION
from .yahoo_imap import YahooImapMessage, imap_message_identity

HISTORICAL_MAX_JOB_ID = 7718
HISTORICAL_MAX_IMPORT_ID = 4
LIVE_DATABASE = (Path(__file__).resolve().parents[3] / "data" / "jobs.db").resolve()
INCIDENT_CHECKSUM = "e82d1fa0e4e751ec14b36cf82298e0931c81631698704c0d1152bae7bfe52bc1"
INCIDENT_UIDS = (53314, 53336, 53355, 53375, 53386)
RECOVERY_CONFIRMATION_TOKEN = "YAHOO-INCIDENT-RECOVERY"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_digest(connection: sqlite3.Connection, table: str) -> str:
    columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
    order = ", ".join(f'"{column}"' for column in columns)
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()
    payload = json.dumps({"columns": columns, "rows": rows}, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def database_digests(path: Path) -> dict[str, str]:
    """Return deterministic table digests through a read-only SQLite connection."""
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {table: _table_digest(connection, table) for table in tables}


@dataclass(frozen=True)
class MessageVerification:
    uid: int
    identity: str
    imported_message: bool
    imap_metadata: bool
    classification: bool
    job_link_stable: bool
    recruiter_links_stable: bool
    interview_links_stable: bool

    @property
    def represented(self) -> bool:
        return all(
            (
                self.imported_message,
                self.imap_metadata,
                self.classification,
                self.job_link_stable,
                self.recruiter_links_stable,
                self.interview_links_stable,
            )
        )


def _message_verification(
    connection: sqlite3.Connection, message: YahooImapMessage
) -> MessageVerification:
    imported = connection.execute(
        "SELECT job_id FROM imported_messages WHERE stable_message_identity=?",
        (message.identity,),
    ).fetchone()
    metadata = connection.execute(
        "SELECT 1 FROM imap_message_metadata WHERE message_identity=?",
        (message.identity,),
    ).fetchone()
    classification = connection.execute(
        "SELECT job_id FROM email_classifications "
        "WHERE message_identity=? AND classifier_version=?",
        (message.identity, CLASSIFIER_VERSION),
    ).fetchone()
    imported_job = imported[0] if imported else None
    classified_job = classification[0] if classification else None
    job_exists = imported_job is None or bool(
        connection.execute("SELECT 1 FROM jobs WHERE id=?", (imported_job,)).fetchone()
    )
    job_stable = bool(imported and classification and imported_job == classified_job and job_exists)
    recruiter_links_stable = not connection.execute(
        "SELECT 1 FROM recruiter_job_links r "
        "LEFT JOIN recruiters p ON p.id=r.recruiter_id "
        "LEFT JOIN jobs j ON j.id=r.job_id "
        "WHERE r.source_message_identity=? AND (p.id IS NULL OR j.id IS NULL)",
        (message.identity,),
    ).fetchone()
    interview_links_stable = not connection.execute(
        "SELECT 1 FROM interview_events e "
        "LEFT JOIN interviews i ON i.id=e.interview_id "
        "WHERE e.source_message_identity=? "
        "AND (e.interview_id IS NOT NULL AND i.id IS NULL)",
        (message.identity,),
    ).fetchone()
    return MessageVerification(
        uid=message.uid,
        identity=message.identity,
        imported_message=bool(imported),
        imap_metadata=bool(metadata),
        classification=bool(classification),
        job_link_stable=job_stable,
        recruiter_links_stable=recruiter_links_stable,
        interview_links_stable=interview_links_stable,
    )


def verify_imap_batch_read_only(
    database: Path, messages: Sequence[YahooImapMessage]
) -> dict[str, Any]:
    """Verify persisted batch evidence without invoking any persistence code."""
    checksum_before = sha256_file(database)
    digests_before = database_digests(database)
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        import_count = int(connection.execute("SELECT COUNT(*) FROM email_imports").fetchone()[0])
        records = [_message_verification(connection, message) for message in messages]
    digests_after = database_digests(database)
    checksum_after = sha256_file(database)
    missing = [record.uid for record in records if not record.represented]
    passed = not missing and checksum_before == checksum_after and digests_before == digests_after
    return {
        "performed": True,
        "mode": "read-only",
        "passed": passed,
        "candidate_count": len(messages),
        "represented_count": len(messages) - len(missing),
        "missing_uids": missing,
        "email_import_count": import_count,
        "email_import_row_created": False,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "file_unchanged": checksum_before == checksum_after,
        "logical_digests_unchanged": digests_before == digests_after,
        "database_writes": 0,
        "records": [asdict(record) | {"represented": record.represented} for record in records],
    }


def verify_yahoo_batch_read_only(
    database: Path, messages: Sequence[YahooImapMessage]
) -> dict[str, Any]:
    """Compatibility wrapper for the original Yahoo incident verifier."""
    if any(message.provider != "yahoo" for message in messages):
        raise ValueError("Yahoo verification accepts only Yahoo transport messages")
    return verify_imap_batch_read_only(database, messages)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Dry-run evidence must contain a JSON object")
    return value


def _classification_shortfall(expected: dict[str, int], actual: dict[str, int]) -> dict[str, int]:
    return {
        classification: count - actual.get(classification, 0)
        for classification, count in expected.items()
        if count > actual.get(classification, 0)
    }


def analyze_incident(database: Path, dry_run_evidence: Path) -> dict[str, Any]:
    """Analyze the partial Sprint 10 database using only sanitized evidence."""
    checksum_before = sha256_file(database)
    dry_run = _read_json(dry_run_evidence)
    first_uid = int(dry_run["first_uid"])
    last_uid = int(dry_run["last_uid_attempted"])
    expected_uids = list(range(first_uid, last_uid + 1))
    if len(expected_uids) != int(dry_run["batch_selected_count"]):
        raise ValueError("Dry-run UID range is not a contiguous selected batch")
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT m.imap_uid,m.uidvalidity,m.folder,m.account_namespace,"
            "i.stable_message_identity,i.source_import_id,i.id imported_message_id,"
            "c.classification,i.job_id FROM imap_message_metadata m "
            "JOIN imported_messages i ON i.stable_message_identity=m.message_identity "
            "JOIN email_classifications c ON c.message_identity=m.message_identity "
            "WHERE m.provider='yahoo' ORDER BY m.imap_uid"
        ).fetchall()
        if not rows:
            raise ValueError("Incident database contains no Yahoo IMAP evidence")
        present = {int(row["imap_uid"]): row for row in rows}
        missing_uids = [uid for uid in expected_uids if uid not in present]
        actual_classifications = dict(
            connection.execute(
                "SELECT classification,COUNT(*) FROM email_classifications GROUP BY classification"
            )
        )
        shortfall = _classification_shortfall(dry_run["classifications"], actual_classifications)
        second_import_id = int(
            connection.execute(
                "SELECT MAX(id) FROM email_imports WHERE mailbox_name='yahoo'"
            ).fetchone()[0]
        )
        second_pass = [row for row in rows if int(row["source_import_id"]) == second_import_id]
        checkpoint_count = int(
            connection.execute("SELECT COUNT(*) FROM imap_sync_checkpoints").fetchone()[0]
        )
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "jobs",
                "email_imports",
                "imported_messages",
                "email_classifications",
                "imap_message_metadata",
                "recruiters",
                "recruiter_company_links",
                "recruiter_email_addresses",
                "recruiter_job_links",
                "interviews",
                "interview_events",
                "imap_sync_checkpoints",
            )
        }
    account = str(rows[0]["account_namespace"])
    folder = str(rows[0]["folder"])
    uidvalidity = str(rows[0]["uidvalidity"])
    missing_classification = next(iter(shortfall), "UNKNOWN") if len(shortfall) == 1 else "UNKNOWN"
    unresolved = [
        {
            "provider": "yahoo",
            "folder": folder,
            "uid": uid,
            "uidvalidity": uidvalidity,
            "classification": missing_classification,
            "reason_category": "persistence_failure",
            "reason": (
                "message was fetched and classified in the dry run, but the live importer "
                "committed no provenance or per-message exception ledger"
            ),
            "stable_identity": imap_message_identity(
                account_namespace=account,
                folder=folder,
                uidvalidity=uidvalidity,
                uid=uid,
            ),
        }
        for uid in missing_uids
    ]
    run_scope = (
        f"yahoo|{account}|{folder}|{dry_run['since_date']}|{uidvalidity}|{first_uid}|{last_uid}"
    )
    checksum_after = sha256_file(database)
    return {
        "mode": "incident-analysis",
        "database_checksum": checksum_before,
        "database_unchanged": checksum_before == checksum_after,
        "deterministic_run_identifier": hashlib.sha256(run_scope.encode()).hexdigest(),
        "scope": {
            "provider": "yahoo",
            "folder": folder,
            "since_date": dry_run["since_date"],
            "uidvalidity": uidvalidity,
            "first_uid": first_uid,
            "last_uid": last_uid,
        },
        "row_counts": counts,
        "present_count": len(present),
        "missing_count": len(missing_uids),
        "missing_uids": missing_uids,
        "unresolved_messages": unresolved,
        "classification_shortfall": shortfall,
        "second_pass_only": [
            {
                "uid": int(row["imap_uid"]),
                "stable_identity": row["stable_message_identity"],
                "classification": row["classification"],
                "imported_message_id": int(row["imported_message_id"]),
                "job_id": row["job_id"],
            }
            for row in second_pass
        ],
        "rerun_would_insert_count": len(missing_uids),
        "checkpoint_present": bool(checkpoint_count),
        "checkpoint_advancement_safe": not missing_uids,
        "database_writes": 0,
    }


def validate_recovery_gate(
    database: Path,
    incident_backup_metadata: Path,
    dry_run_evidence: Path,
    *,
    confirmation: str,
    expected_database: Path = LIVE_DATABASE,
    expected_checksum: str = INCIDENT_CHECKSUM,
) -> dict[str, Any]:
    """Validate the exact partial incident state without connecting or writing."""
    resolved = database.resolve()
    if resolved != expected_database.resolve():
        raise ValueError("Recovery requires the exact approved incident database path")
    if confirmation != RECOVERY_CONFIRMATION_TOKEN:
        raise ValueError("Recovery confirmation token is invalid")
    checksum = sha256_file(resolved)
    if checksum != expected_checksum:
        raise ValueError("Incident database checksum does not match the approved value")
    metadata = _read_json(incident_backup_metadata)
    backup = Path(str(metadata.get("path", ""))).expanduser().resolve()
    if not backup.is_file() or sha256_file(backup) != metadata.get("checksum_sha256"):
        raise ValueError("Incident backup is missing or failed checksum verification")
    if metadata.get("alembic_revision") != "20260712_0006":
        raise ValueError("Incident backup must be at revision 20260712_0006")
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        checkpoint_count = int(
            connection.execute("SELECT COUNT(*) FROM imap_sync_checkpoints").fetchone()[0]
        )
    analysis = analyze_incident(resolved, dry_run_evidence)
    if revision not in {"20260712_0006", "20260808_0007"} or integrity != "ok" or foreign_keys:
        raise ValueError("Incident database health validation failed")
    if checkpoint_count or tuple(analysis["missing_uids"]) != INCIDENT_UIDS:
        raise ValueError("Incident recovery scope no longer matches the approved five UIDs")
    return {
        "mode": "incident-recovery-preflight",
        "database": str(resolved),
        "checksum": checksum,
        "revision": revision,
        "backup": str(backup),
        "missing_uids": list(INCIDENT_UIDS),
        "checkpoint_count": checkpoint_count,
        "network_connections": 0,
        "database_writes": 0,
    }


def apply_missing_recovery(
    database: Path,
    messages: Sequence[YahooImapMessage],
    importer: Callable[[Sequence[YahooImapMessage]], dict[str, object]],
    *,
    accepted_unavailable_uids: Sequence[int] = (),
) -> dict[str, Any]:
    """Apply available incident UIDs and preserve explicitly accepted server exclusions."""
    unavailable = tuple(sorted(accepted_unavailable_uids))
    if not set(unavailable) <= set(INCIDENT_UIDS):
        raise ValueError("Unavailable UIDs must be within the approved incident scope")
    expected = tuple(uid for uid in INCIDENT_UIDS if uid not in unavailable)
    if tuple(sorted(message.uid for message in messages)) != expected:
        raise ValueError("Recovery input does not match the available approved incident UIDs")
    before_counts: dict[str, int]
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        before_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("jobs", "email_imports", "imported_messages", "email_classifications")
        }
    imported = importer(messages)
    verification = verify_imap_batch_read_only(database, messages)
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        after_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in before_counts
        }
    passed = (
        imported["accepted_count"] == len(expected)
        and imported["failure_count"] == 0
        and verification["passed"]
        and after_counts["jobs"] == before_counts["jobs"]
        and after_counts["email_imports"] == before_counts["email_imports"] + 1
        and after_counts["imported_messages"] == before_counts["imported_messages"] + len(expected)
        and after_counts["email_classifications"]
        == before_counts["email_classifications"] + len(expected)
    )
    if not passed:
        raise RuntimeError("Controlled missing-UID recovery failed validation")
    return {
        "mode": "missing-uid-recovery",
        "passed": True,
        "uids": list(expected),
        "accepted_unavailable_uids": list(unavailable),
        "import_result": imported,
        "read_only_verification": verification,
        "before_counts": before_counts,
        "after_counts": after_counts,
    }


def recovery_scope(database: Path) -> dict[str, Any]:
    """Return a deletion plan restricted to post-baseline Yahoo evidence."""
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        import_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM email_imports WHERE id>? AND mailbox_name='yahoo' ORDER BY id",
                (HISTORICAL_MAX_IMPORT_ID,),
            )
        ]
        identities = (
            [
                str(row[0])
                for row in connection.execute(
                    "SELECT stable_message_identity FROM imported_messages "
                    f"WHERE source_import_id IN ({','.join('?' for _ in import_ids)}) "
                    "ORDER BY stable_message_identity",
                    import_ids,
                )
            ]
            if import_ids
            else []
        )
        job_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM jobs WHERE id>? ORDER BY id", (HISTORICAL_MAX_JOB_ID,)
            )
        ]
    if any(value <= HISTORICAL_MAX_IMPORT_ID for value in import_ids):
        raise ValueError("Recovery scope includes a historical email_import row")
    if any(value <= HISTORICAL_MAX_JOB_ID for value in job_ids):
        raise ValueError("Recovery scope includes a historical job row")
    return {
        "historical_max_job_id": HISTORICAL_MAX_JOB_ID,
        "historical_max_import_id": HISTORICAL_MAX_IMPORT_ID,
        "email_import_ids": import_ids,
        "message_identities": identities,
        "job_ids": job_ids,
        "historical_rows_in_scope": 0,
    }


def disposable_copy(source: Path, destination: Path) -> Path:
    """Create a copy for later recovery rehearsal while refusing in-place operation."""
    if source.resolve() == destination.resolve():
        raise ValueError("Disposable recovery destination must differ from the source database")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def rollback_incident_copy(database: Path) -> dict[str, Any]:
    """Remove only post-baseline Yahoo rows from a disposable incident copy."""
    resolved = database.resolve()
    if resolved == LIVE_DATABASE:
        raise ValueError("Incident rollback is forbidden on the live database")
    scope = recovery_scope(resolved)
    expected_jobs = list(range(HISTORICAL_MAX_JOB_ID + 1, HISTORICAL_MAX_JOB_ID + 20))
    expected_imports = [HISTORICAL_MAX_IMPORT_ID + 1, HISTORICAL_MAX_IMPORT_ID + 2]
    if scope["email_import_ids"] != expected_imports or scope["job_ids"] != expected_jobs:
        raise ValueError("Disposable database does not match the exact Sprint 10 rollback scope")
    if len(scope["message_identities"]) != 95:
        raise ValueError("Disposable database does not contain the expected 95 Yahoo identities")
    identities = list(scope["message_identities"])
    import_ids = list(scope["email_import_ids"])
    before = {
        "checksum": sha256_file(resolved),
        "digests": database_digests(resolved),
    }
    with sqlite3.connect(resolved) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        dependent_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "recruiters",
                "recruiter_company_links",
                "recruiter_email_addresses",
                "recruiter_job_links",
                "interviews",
                "interview_events",
                "imap_sync_checkpoints",
            )
        }
        if any(dependent_counts.values()):
            connection.rollback()
            raise ValueError("Disposable rollback refuses non-empty downstream evidence tables")
        if identities:
            placeholders = ",".join("?" for _ in identities)
            connection.execute(
                f"DELETE FROM interview_events WHERE source_message_identity IN ({placeholders})",
                identities,
            )
            connection.execute(
                f"DELETE FROM interviews WHERE first_source_message_identity IN ({placeholders})",
                identities,
            )
            connection.execute(
                "DELETE FROM recruiter_job_links "
                f"WHERE source_message_identity IN ({placeholders})",
                identities,
            )
            connection.execute(
                f"DELETE FROM email_classifications WHERE message_identity IN ({placeholders})",
                identities,
            )
            connection.execute(
                f"DELETE FROM imap_message_metadata WHERE message_identity IN ({placeholders})",
                identities,
            )
            connection.execute(
                f"DELETE FROM imported_messages WHERE stable_message_identity IN ({placeholders})",
                identities,
            )
        connection.execute("DELETE FROM recruiter_job_links")
        connection.execute("DELETE FROM recruiter_email_addresses")
        connection.execute("DELETE FROM recruiter_company_links")
        connection.execute("DELETE FROM recruiters")
        connection.execute("DELETE FROM interview_events")
        connection.execute("DELETE FROM interviews")
        connection.execute("DELETE FROM imap_sync_checkpoints WHERE provider='yahoo'")
        connection.execute("DELETE FROM jobs WHERE id>?", (HISTORICAL_MAX_JOB_ID,))
        if import_ids:
            placeholders = ",".join("?" for _ in import_ids)
            connection.execute(
                f"DELETE FROM email_imports WHERE id IN ({placeholders})", import_ids
            )
        connection.commit()
        historical_jobs = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE id<=?", (HISTORICAL_MAX_JOB_ID,)
            ).fetchone()[0]
        )
        historical_imports = int(
            connection.execute(
                "SELECT COUNT(*) FROM email_imports WHERE id<=?", (HISTORICAL_MAX_IMPORT_ID,)
            ).fetchone()[0]
        )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if historical_jobs != HISTORICAL_MAX_JOB_ID or historical_imports != HISTORICAL_MAX_IMPORT_ID:
        raise RuntimeError("Disposable rollback did not preserve the historical baseline")
    if integrity != "ok" or foreign_keys:
        raise RuntimeError("Disposable rollback failed database validation")
    return {
        "mode": "disposable-rollback",
        "database": str(resolved),
        "scope": scope,
        "before_checksum": before["checksum"],
        "after_checksum": sha256_file(resolved),
        "historical_jobs": historical_jobs,
        "historical_email_imports": historical_imports,
        "integrity_check": integrity,
        "foreign_key_violations": [],
    }
