"""SQLite checkpoint persistence for approval-gated IMAP synchronization."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

EXPECTED_REVISION = "20260823_0008"


class UidValidityChangedError(RuntimeError):
    """Raised when a mailbox UID namespace changes and needs explicit rescan approval."""


@dataclass(frozen=True)
class ImapCheckpoint:
    provider: str
    account_namespace: str
    folder: str
    since_date: date
    uidvalidity: str
    last_successful_uid: int
    sync_started_at: datetime
    sync_completed_at: datetime | None
    scanned_count: int
    accepted_count: int
    skipped_count: int
    failure_count: int


def verify_sync_database(path: Path) -> Path:
    """Require a disposable database at the Yahoo IMAP schema revision."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Sync database does not exist: {resolved}")
    uri = f"file:{resolved}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    required = {"imap_sync_checkpoints", "imap_message_metadata"}
    if revision != (EXPECTED_REVISION,) or not required.issubset(tables):
        raise ValueError(f"Sync database must be at Alembic revision {EXPECTED_REVISION}")
    return resolved


def read_checkpoint(
    path: Path,
    *,
    provider: str,
    account_namespace: str,
    folder: str,
    since_date: date,
) -> ImapCheckpoint | None:
    """Read one account/folder checkpoint without changing the database."""
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            """SELECT provider, account_namespace, folder, since_date, uidvalidity,
                      last_successful_uid, sync_started_at, sync_completed_at,
                      scanned_count, accepted_count, skipped_count, failure_count
               FROM imap_sync_checkpoints
               WHERE provider=? AND account_namespace=? AND folder=? AND since_date=?""",
            (provider, account_namespace, folder, since_date.isoformat()),
        ).fetchone()
    if row is None:
        return None
    return ImapCheckpoint(
        provider=str(row[0]),
        account_namespace=str(row[1]),
        folder=str(row[2]),
        since_date=date.fromisoformat(str(row[3])),
        uidvalidity=str(row[4]),
        last_successful_uid=int(row[5]),
        sync_started_at=datetime.fromisoformat(str(row[6])),
        sync_completed_at=datetime.fromisoformat(str(row[7])) if row[7] else None,
        scanned_count=int(row[8]),
        accepted_count=int(row[9]),
        skipped_count=int(row[10]),
        failure_count=int(row[11]),
    )


def require_stable_uidvalidity(checkpoint: ImapCheckpoint | None, current_uidvalidity: str) -> None:
    """Stop rather than silently crossing into a new IMAP UID namespace."""
    if checkpoint and checkpoint.uidvalidity != current_uidvalidity:
        raise UidValidityChangedError(
            "Yahoo IMAP UIDVALIDITY changed; explicit rescan approval is required"
        )


def write_checkpoint(path: Path, checkpoint: ImapCheckpoint) -> None:
    """Persist only completed-run checkpoint and counters."""
    now = checkpoint.sync_completed_at or checkpoint.sync_started_at
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO imap_sync_checkpoints (
                   provider, account_namespace, folder, since_date, uidvalidity,
                   last_successful_uid, sync_started_at, sync_completed_at,
                   scanned_count, accepted_count, skipped_count, failure_count,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, account_namespace, folder, since_date) DO UPDATE SET
                   uidvalidity=excluded.uidvalidity,
                   last_successful_uid=excluded.last_successful_uid,
                   sync_started_at=excluded.sync_started_at,
                   sync_completed_at=excluded.sync_completed_at,
                   scanned_count=excluded.scanned_count,
                   accepted_count=excluded.accepted_count,
                   skipped_count=excluded.skipped_count,
                   failure_count=excluded.failure_count,
                   updated_at=excluded.updated_at""",
            (
                checkpoint.provider,
                checkpoint.account_namespace,
                checkpoint.folder,
                checkpoint.since_date.isoformat(),
                checkpoint.uidvalidity,
                checkpoint.last_successful_uid,
                checkpoint.sync_started_at.isoformat(sep=" "),
                (
                    checkpoint.sync_completed_at.isoformat(sep=" ")
                    if checkpoint.sync_completed_at
                    else None
                ),
                checkpoint.scanned_count,
                checkpoint.accepted_count,
                checkpoint.skipped_count,
                checkpoint.failure_count,
                checkpoint.sync_started_at.isoformat(sep=" "),
                now.isoformat(sep=" "),
            ),
        )
