from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from backend.app.database.migration_readiness import (
    BASELINE_REVISION,
    HEAD_REVISION,
    LIVE_DATABASE,
    collect_evidence,
    create_backup,
    ensure_not_live_mutation,
    preflight,
    rehearse,
    run_alembic,
    table_digest,
    write_duplicate_report,
)


def create_database(path: Path, revision: str = BASELINE_REVISION) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("JOBS_DB_PATH", str(path))
        command.upgrade(config, revision)


def insert_minimal_job(
    path: Path,
    *,
    linkedin_job_id: str = "fixture-job",
    message_id: str = "",
    company: str = "Sanitized Company",
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO jobs (
                linkedin_job_id, title, company, location, salary_text,
                applicant_count_is_over, applicant_text, easy_apply, promoted,
                posted_text, work_mode, description, url, source, status, notes,
                score, first_seen_at, last_seen_at, confirmation_message_id
            ) VALUES (?, 'Product Manager', ?, '', '', 0, '', 0, 0, '', '', '', '',
                      'linkedin', 'saved', 'preserve', 50, '2026-01-01', '2026-01-01', ?)""",
            (linkedin_job_id, company, message_id),
        )
        connection.commit()


def test_successful_read_only_preflight(tmp_path: Path) -> None:
    database = tmp_path / "baseline.db"
    create_database(database)

    result = preflight(database)

    assert result.compatible
    assert result.state == f"versioned:{BASELINE_REVISION}"
    assert result.evidence.integrity_check == ["ok"]
    assert result.evidence.foreign_key_violations == []


def test_preflight_reports_schema_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "mismatch.db"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX ix_jobs_linkedin_job_id")

    result = preflight(database)

    assert not result.compatible
    assert "missing expected unique index" in " ".join(result.errors)


def test_live_database_write_protection() -> None:
    with pytest.raises(ValueError, match="Refusing to modify historical database"):
        ensure_not_live_mutation(LIVE_DATABASE)
    with pytest.raises(ValueError, match="Refusing to modify historical database"):
        run_alembic(LIVE_DATABASE, "stamp", BASELINE_REVISION)


def test_backup_creation_records_and_verifies_evidence(tmp_path: Path) -> None:
    database = tmp_path / "source" / "baseline.db"
    database.parent.mkdir()
    create_database(database)
    insert_minimal_job(database)

    backup, metadata = create_backup(database, tmp_path / "backups")
    backup_evidence = collect_evidence(backup)

    assert backup.exists()
    assert metadata.exists()
    assert backup_evidence.row_counts["jobs"] == 1
    assert table_digest(database, "jobs") == table_digest(backup, "jobs")
    assert table_digest(database, "email_imports") == table_digest(backup, "email_imports")
    assert json.loads(metadata.read_text())["integrity_check"] == ["ok"]


def test_rehearsal_stamps_upgrades_reruns_preserves_and_rolls_back(tmp_path: Path) -> None:
    database = tmp_path / "source" / "historical.db"
    database.parent.mkdir()
    create_database(database)
    insert_minimal_job(database)
    before_digest = table_digest(database, "jobs")

    result = rehearse(database, tmp_path / "rehearsal")
    copy_path = Path(result["copy"])

    assert result["upgraded"]["alembic_revision"] == HEAD_REVISION
    assert "imported_messages" in result["upgraded"]["tables"]
    assert result["rolled_back"]["alembic_revision"] == BASELINE_REVISION
    assert "imported_messages" not in result["rolled_back"]["tables"]
    assert result["before"]["row_counts"]["jobs"] == 1
    assert result["rolled_back"]["row_counts"]["jobs"] == 1
    assert table_digest(copy_path, "jobs") == before_digest


def test_duplicate_report_contains_field_differences_and_categories(tmp_path: Path) -> None:
    database = tmp_path / "duplicates.db"
    create_database(database)
    insert_minimal_job(database, linkedin_job_id="one", message_id="duplicate@example.com")
    insert_minimal_job(
        database,
        linkedin_job_id="two",
        message_id="duplicate@example.com",
        company="Different Company",
    )
    report = tmp_path / "duplicate-report.csv"

    groups = write_duplicate_report(database, report)
    with report.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert groups == 1
    assert rows[0]["message_id"] == "duplicate@example.com"
    assert "company" in rows[0]["fields_that_differ"]
    assert rows[0]["recommended_category"] == "conflicting record requiring manual review"
