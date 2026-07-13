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

    assert tables == {
        "alembic_version",
        "email_classifications",
        "email_imports",
        "imported_messages",
        "jobs",
        "recruiter_company_links",
        "recruiter_email_addresses",
        "recruiter_job_links",
        "recruiters",
    }
    assert revision == ("20260712_0004",)


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
            connection.execute("SELECT COUNT(*) FROM email_classifications").fetchone(),
            connection.execute("SELECT COUNT(*) FROM recruiters").fetchone(),
            connection.execute("SELECT COUNT(*) FROM recruiter_job_links").fetchone(),
        )

    assert counts == ((1,), (1,), (0,), (0,), (0,), (0,))


def test_alembic_uses_database_path_secondary_override(tmp_path: Path) -> None:
    database_path = tmp_path / "database-path-override.db"
    environment = os.environ.copy()
    environment.pop("JOBS_DB_PATH", None)
    environment["DATABASE_PATH"] = str(database_path)

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == ("20260712_0004",)


def test_classification_migration_from_live_revision_is_additive(tmp_path: Path) -> None:
    database_path = tmp_path / "live-revision-copy.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260712_0002"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        before = {
            "jobs": connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "email_imports": connection.execute("SELECT COUNT(*) FROM email_imports").fetchone()[0],
            "imported_messages": connection.execute(
                "SELECT COUNT(*) FROM imported_messages"
            ).fetchone()[0],
        }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        classifications = connection.execute(
            "SELECT COUNT(*) FROM email_classifications"
        ).fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert after == before
    assert classifications == 0
    assert revision == "20260712_0004"


def test_recruiter_migration_from_live_revision_is_additive(tmp_path: Path) -> None:
    database_path = tmp_path / "recruiter-migration.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260712_0003"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("jobs", "email_imports", "imported_messages", "email_classifications")
        }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        recruiter_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "recruiters",
                "recruiter_company_links",
                "recruiter_email_addresses",
                "recruiter_job_links",
            )
        }
        foreign_tables = {
            row[2] for row in connection.execute("PRAGMA foreign_key_list('recruiter_job_links')")
        }
        indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list('recruiter_job_links')")
        }
        unique_index = next(name for name, unique in indexes.items() if unique)
        unique_columns = [
            row[2] for row in connection.execute(f'PRAGMA index_info("{unique_index}")')
        ]
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recruiter_job_links'"
        ).fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert after == before
    assert set(recruiter_counts.values()) == {0}
    assert foreign_tables == {"recruiters", "jobs", "imported_messages"}
    assert indexes["ix_recruiter_job_job_id"] is False
    assert unique_columns == ["recruiter_id", "job_id", "relationship_type"]
    assert "ck_recruiter_job_relationship_type" in table_sql
    assert revision == "20260712_0004"
