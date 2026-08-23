"""Preservation-first reconciliation of a legacy LinkedIn submission ledger."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LEGACY_APPLICATION_SOURCE = "linkedin_extension_legacy"
_REQUIRED_JOB_COLUMNS = {
    "linkedin_job_id",
    "source",
    "status",
    "first_seen_at",
    "last_seen_at",
    "applied_at",
    "application_source",
}


@dataclass(frozen=True)
class ReconciliationReport:
    source_records: int
    created: int
    marked_applied: int
    dated: int
    already_reconciled: int
    skipped_existing_outcome: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _job_columns(connection: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}


def _assert_compatible(connection: sqlite3.Connection, path: Path) -> set[str]:
    columns = _job_columns(connection)
    missing = _REQUIRED_JOB_COLUMNS - columns
    if missing:
        raise ValueError(f"{path} is missing required jobs columns: {', '.join(sorted(missing))}")
    return columns


def _source_rows(connection: sqlite3.Connection, columns: set[str]) -> list[dict[str, Any]]:
    selected = ", ".join(sorted(columns))
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"SELECT {selected} FROM jobs "
        "WHERE source = 'linkedin' AND status = 'applied' "
        "ORDER BY COALESCE(applied_at, first_seen_at), id"
    ).fetchall()
    return [dict(row) for row in rows]


def reconcile_legacy_linkedin_submissions(
    source_database: Path, destination_database: Path
) -> ReconciliationReport:
    """Copy historical applied LinkedIn rows without replacing richer current data."""
    source_database = source_database.resolve()
    destination_database = destination_database.resolve()
    if source_database == destination_database:
        raise ValueError("source and destination databases must be different")
    if not source_database.is_file() or not destination_database.is_file():
        raise ValueError("source and destination databases must exist")

    with _connect_readonly(source_database) as source:
        source_columns = _assert_compatible(source, source_database)
        rows = _source_rows(source, source_columns)

    created = marked_applied = dated = already = skipped = 0
    with sqlite3.connect(destination_database) as destination:
        destination.row_factory = sqlite3.Row
        destination_columns = _assert_compatible(destination, destination_database)
        common_columns = sorted((source_columns & destination_columns) - {"id"})
        destination.execute("BEGIN IMMEDIATE")
        for row in rows:
            identifier = str(row["linkedin_job_id"])
            applied_at = row.get("applied_at") or row.get("first_seen_at")
            existing = destination.execute(
                "SELECT id, status, applied_at, application_source "
                "FROM jobs WHERE linkedin_job_id = ?",
                (identifier,),
            ).fetchone()
            if existing is None:
                values = {column: row.get(column) for column in common_columns}
                values.update(
                    {
                        "source": "linkedin",
                        "status": "applied",
                        "applied_at": applied_at,
                        "application_source": LEGACY_APPLICATION_SOURCE,
                    }
                )
                columns = sorted(values)
                placeholders = ", ".join("?" for _ in columns)
                destination.execute(
                    f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders})",
                    [values[column] for column in columns],
                )
                created += 1
                dated += int(bool(applied_at))
                continue

            updates: dict[str, Any] = {}
            status = str(existing["status"] or "").casefold()
            if status in {"new", "saved"}:
                updates["status"] = "applied"
                marked_applied += 1
            elif status not in {"applied", "recruiter", "interview", "offer", "rejected"}:
                skipped += 1
            if not existing["applied_at"] and applied_at:
                updates["applied_at"] = applied_at
                dated += 1
            if not str(existing["application_source"] or "").strip():
                updates["application_source"] = LEGACY_APPLICATION_SOURCE
            elif existing["application_source"] == LEGACY_APPLICATION_SOURCE and not updates:
                already += 1
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                destination.execute(
                    f"UPDATE jobs SET {assignments} WHERE id = ?",
                    [*updates.values(), existing["id"]],
                )
        destination.commit()
    return ReconciliationReport(len(rows), created, marked_applied, dated, already, skipped)
