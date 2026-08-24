from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.app.services.evidence_review import list_unlinked_evidence
from fastapi.testclient import TestClient


def test_unlinked_evidence_queue_returns_local_review_metadata_without_body(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE email_classifications (
                message_identity TEXT, job_id INTEGER, classification TEXT, confidence REAL,
                classifier_version TEXT, reason_json TEXT, created_at TEXT
            );
            CREATE TABLE imported_messages (
                stable_message_identity TEXT, provider TEXT, source_import_id INTEGER,
                imported_at TEXT
            );
            CREATE TABLE email_imports (id INTEGER, mailbox_name TEXT);
            CREATE TABLE imap_message_metadata (
                message_identity TEXT, provider TEXT, account_namespace TEXT,
                imap_internal_date TEXT, received_at TEXT, subject TEXT, sender TEXT, text_body TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO email_classifications VALUES (?, NULL, ?, ?, ?, ?, ?)",
            (
                "v1:review",
                "INTERVIEW_INVITATION",
                0.99,
                "deterministic-v1",
                json.dumps({"matched_signals": ["subject=interview"]}),
                "2026-08-22T10:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO imported_messages VALUES ('v1:review', 'gmail', 1, '2026-08-22T10:00:00')"
        )
        connection.execute("INSERT INTO email_imports VALUES (1, 'gmail')")
        connection.execute(
            "INSERT INTO imap_message_metadata VALUES "
            "('v1:review', 'gmail', 'account@example.com', '2026-08-22T09:00:00', NULL, "
            "'Private subject', 'sender@example.com', 'Private message body')"
        )

    result = list_unlinked_evidence(database)

    assert result["total_unlinked"] == 1
    assert result["context_available"] == 1
    assert result["context_unavailable"] == 0
    assert result["actionable_only"] is False
    assert result["items"] == [
        {
            "message_identity": "v1:review",
            "classification": "INTERVIEW_INVITATION",
            "confidence": 0.99,
            "classifier_version": "deterministic-v1",
            "provider": "gmail",
            "account_namespace": "account@example.com",
            "occurred_at": "2026-08-22T09:00:00",
            "subject": "Private subject",
            "sender": "sender@example.com",
            "matched_signals": ["subject=interview"],
        }
    ]
    assert "Private message body" not in json.dumps(result)

    actionable = list_unlinked_evidence(database, actionable_only=True)

    assert actionable["actionable_only"] is True
    assert actionable["context_available"] == 1
    assert actionable["returned"] == 1


def test_actionable_queue_excludes_records_without_retained_review_context(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE email_classifications (
                message_identity TEXT, job_id INTEGER, classification TEXT, confidence REAL,
                classifier_version TEXT, reason_json TEXT, created_at TEXT
            );
            CREATE TABLE imported_messages (
                stable_message_identity TEXT, provider TEXT, source_import_id INTEGER,
                imported_at TEXT
            );
            CREATE TABLE email_imports (id INTEGER, mailbox_name TEXT);
            CREATE TABLE imap_message_metadata (
                message_identity TEXT, provider TEXT, account_namespace TEXT,
                imap_internal_date TEXT, received_at TEXT, subject TEXT, sender TEXT
            );
            INSERT INTO email_classifications VALUES
                ('v1:old', NULL, 'REJECTION', 0.99, 'v1', '[]', '2026-08-22');
            """
        )

    result = list_unlinked_evidence(database, actionable_only=True)

    assert result["total_unlinked"] == 1
    assert result["context_available"] == 0
    assert result["context_unavailable"] == 1
    assert result["items"] == []


def test_unlinked_evidence_api_is_read_only(isolated_app: tuple[TestClient, Path]) -> None:
    client, _ = isolated_app

    response = client.get("/analytics/unlinked-evidence")

    assert response.status_code == 200
    assert response.json() == {
        "total_unlinked": 0,
        "context_available": 0,
        "context_unavailable": 0,
        "actionable_only": False,
        "returned": 0,
        "items": [],
    }


def test_dashboard_exposes_content_free_review_queue_and_safe_interview_view() -> None:
    root = Path(__file__).resolve().parents[1]
    page = (root / "backend" / "static" / "index.html").read_text()
    script = (root / "backend" / "static" / "app.js").read_text()

    assert 'data-tab="review"' in page
    assert "Evidence Review Queue" in page
    assert "Scheduled records (recommended)" in page
    assert "Unscheduled evidence" in page
    assert "/static/app.js?v=review-queue-2" in page
    assert "actionable_only=true" in script
    assert "fetch('/analytics/evidence-links'" in script
    assert "safeInterviewRole" in script


def test_unlinked_evidence_tolerates_legacy_list_reason_json(tmp_path: Path) -> None:
    """Historical classifications may store their matched signals as a JSON list."""
    database = tmp_path / "review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE email_classifications (
                message_identity TEXT, job_id INTEGER, classification TEXT, confidence REAL,
                classifier_version TEXT, reason_json TEXT, created_at TEXT
            );
            CREATE TABLE imported_messages (
                stable_message_identity TEXT, provider TEXT, source_import_id INTEGER,
                imported_at TEXT
            );
            CREATE TABLE email_imports (id INTEGER, mailbox_name TEXT);
            CREATE TABLE imap_message_metadata (
                message_identity TEXT, provider TEXT, account_namespace TEXT,
                imap_internal_date TEXT, received_at TEXT, subject TEXT, sender TEXT
            );
            INSERT INTO email_classifications VALUES
                ('v1:legacy', NULL, 'RECRUITER_REPLY', 0.9, 'v1',
                 '["subject=reply"]', '2026-08-23T00:00:00');
            """
        )

    result = list_unlinked_evidence(database)

    assert result["total_unlinked"] == 1
    assert result["context_available"] == 0
    assert result["items"][0]["message_identity"] == "v1:legacy"
    assert result["items"][0]["matched_signals"] == []


def test_reviewed_link_is_additive_and_removes_only_that_item_from_queue(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE email_classifications (
                message_identity TEXT, job_id INTEGER, classification TEXT, confidence REAL,
                classifier_version TEXT, reason_json TEXT, created_at TEXT
            );
            CREATE TABLE imported_messages (
                stable_message_identity TEXT, provider TEXT,
                source_import_id INTEGER, imported_at TEXT
            );
            CREATE TABLE email_imports (id INTEGER, mailbox_name TEXT);
            CREATE TABLE imap_message_metadata (
                message_identity TEXT, provider TEXT, account_namespace TEXT,
                imap_internal_date TEXT, received_at TEXT, subject TEXT, sender TEXT
            );
            CREATE TABLE jobs (id INTEGER PRIMARY KEY);
            CREATE TABLE evidence_job_links (
                message_identity TEXT UNIQUE, job_id INTEGER, link_method TEXT, reason TEXT,
                created_at TEXT, updated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO imported_messages VALUES ('v1:review', 'gmail', 1, '2026-08-23')"
        )
        connection.execute("INSERT INTO jobs VALUES (7)")
        connection.execute(
            "INSERT INTO email_classifications VALUES ('v1:review', NULL, 'REJECTION', "
            "0.99, 'v1', '{}', '2026-08-23')"
        )

    from backend.app.services.evidence_review import create_reviewed_job_link

    created = create_reviewed_job_link(
        database,
        message_identity="v1:review",
        job_id=7,
        reason="Exact requisition shown in review.",
    )

    assert created == {"message_identity": "v1:review", "job_id": 7, "link_method": "reviewed"}
    assert list_unlinked_evidence(database) == {
        "total_unlinked": 0,
        "context_available": 0,
        "context_unavailable": 0,
        "actionable_only": False,
        "returned": 0,
        "items": [],
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT job_id FROM evidence_job_links").fetchone()[0] == 7
        assert connection.execute("SELECT job_id FROM email_classifications").fetchone()[0] is None


def test_company_alias_is_reversible_and_never_changes_jobs(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT);
            CREATE TABLE company_aliases (
                alias_name TEXT, normalized_alias TEXT UNIQUE, canonical_name TEXT, reason TEXT,
                created_at TEXT, updated_at TEXT
            );
            INSERT INTO jobs VALUES (1, 'Google LLC');
            """
        )

    from backend.app.services.evidence_review import create_company_alias

    create_company_alias(
        database,
        alias_name="Google LLC",
        canonical_name="Google",
        reason="Reviewed legal suffix normalization.",
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT company FROM jobs").fetchone()[0] == "Google LLC"
        assert (
            connection.execute("SELECT canonical_name FROM company_aliases").fetchone()[0]
            == "Google"
        )
