from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_baseline_migration_builds_current_two_table_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert tables == {"alembic_version", "email_imports", "imported_messages", "jobs"}
    assert revision == ("20260712_0002",)


def test_upgrade_from_baseline_preserves_existing_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "baseline-upgrade.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260712_0001"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO jobs (
                linkedin_job_id, title, company, location, salary_text,
                applicant_count_is_over, applicant_text, easy_apply, promoted,
                posted_text, work_mode, description, url, source, status, notes,
                score, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "baseline-job",
                "Title",
                "Company",
                "",
                "",
                0,
                "",
                0,
                0,
                "",
                "",
                "",
                "",
                "linkedin",
                "saved",
                "keep",
                50.0,
                "2026-01-01",
                "2026-01-01",
            ),
        )
        connection.execute(
            """INSERT INTO email_imports (
                mailbox_name, source_filename, imported_at, total_messages,
                confirmations_found, matched_jobs, unmatched_jobs
            ) VALUES ('gmail', 'old.mbox', '2026-01-01', 1, 1, 0, 1)"""
        )
        connection.commit()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        counts = (
            connection.execute("SELECT COUNT(*) FROM jobs").fetchone(),
            connection.execute("SELECT COUNT(*) FROM email_imports").fetchone(),
            connection.execute("SELECT COUNT(*) FROM imported_messages").fetchone(),
        )

    assert counts == ((1,), (1,), (0,))
