from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.services.linkedin_submission_ledger import (  # isort: skip
    LEGACY_APPLICATION_SOURCE,
    reconcile_legacy_linkedin_submissions,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                linkedin_job_id TEXT UNIQUE,
                source TEXT,
                status TEXT,
                title TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                applied_at TEXT,
                application_source TEXT
            )
            """
        )


def _legacy_row(connection: sqlite3.Connection, identifier: str, date: str) -> None:
    connection.execute(
        """
        INSERT INTO jobs (
            linkedin_job_id, source, status, title, first_seen_at, last_seen_at,
            applied_at, application_source
        ) VALUES (?, 'linkedin', 'applied', 'Example role', ?, ?, NULL, '')
        """,
        (identifier, date, date),
    )


def test_reconciliation_creates_dated_repeat_safe_ledger_rows(tmp_path: Path) -> None:
    source, destination = tmp_path / "legacy.db", tmp_path / "runtime.db"
    _database(source)
    _database(destination)
    with sqlite3.connect(source) as connection:
        _legacy_row(connection, "one", "2026-07-20T10:00:00")
        _legacy_row(connection, "two", "2026-08-04T10:00:00")

    first = reconcile_legacy_linkedin_submissions(source, destination)
    second = reconcile_legacy_linkedin_submissions(source, destination)

    assert first.to_dict() == {
        "source_records": 2,
        "created": 2,
        "marked_applied": 0,
        "dated": 2,
        "already_reconciled": 0,
        "skipped_existing_outcome": 0,
    }
    assert second.created == 0
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT linkedin_job_id, applied_at, application_source "
            "FROM jobs ORDER BY linkedin_job_id"
        ).fetchall()
    assert rows == [
        ("one", "2026-07-20T10:00:00", LEGACY_APPLICATION_SOURCE),
        ("two", "2026-08-04T10:00:00", LEGACY_APPLICATION_SOURCE),
    ]


def test_reconciliation_only_promotes_unresolved_existing_rows(tmp_path: Path) -> None:
    source, destination = tmp_path / "legacy.db", tmp_path / "runtime.db"
    _database(source)
    _database(destination)
    with sqlite3.connect(source) as connection:
        _legacy_row(connection, "saved", "2026-07-20T10:00:00")
        _legacy_row(connection, "rejected", "2026-07-21T10:00:00")
    with sqlite3.connect(destination) as connection:
        connection.execute(
            "INSERT INTO jobs VALUES (1, 'saved', 'linkedin', 'saved', 'current', '', '', NULL, '')"
        )
        connection.execute(
            "INSERT INTO jobs VALUES "
            "(2, 'rejected', 'linkedin', 'rejected', 'current', '', '', NULL, '')"
        )

    report = reconcile_legacy_linkedin_submissions(source, destination)

    assert report.marked_applied == 1
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT linkedin_job_id, status, applied_at FROM jobs ORDER BY id"
        ).fetchall()
    assert rows == [
        ("saved", "applied", "2026-07-20T10:00:00"),
        ("rejected", "rejected", "2026-07-21T10:00:00"),
    ]


def test_reconciliation_refuses_same_database(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    _database(database)
    with pytest.raises(ValueError, match="must be different"):
        reconcile_legacy_linkedin_submissions(database, database)
