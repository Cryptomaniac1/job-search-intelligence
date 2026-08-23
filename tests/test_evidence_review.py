from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.app.services.evidence_review import list_unlinked_evidence
from fastapi.testclient import TestClient


def test_unlinked_evidence_queue_returns_provenance_without_message_content(tmp_path: Path) -> None:
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
    assert result["items"] == [
        {
            "message_identity": "v1:review",
            "classification": "INTERVIEW_INVITATION",
            "confidence": 0.99,
            "classifier_version": "deterministic-v1",
            "provider": "gmail",
            "account_namespace": "account@example.com",
            "occurred_at": "2026-08-22T09:00:00",
            "matched_signals": ["subject=interview"],
        }
    ]
    assert "Private subject" not in json.dumps(result)
    assert "Private message body" not in json.dumps(result)


def test_unlinked_evidence_api_is_read_only(isolated_app: tuple[TestClient, Path]) -> None:
    client, _ = isolated_app

    response = client.get("/analytics/unlinked-evidence")

    assert response.status_code == 200
    assert response.json() == {"total_unlinked": 0, "returned": 0, "items": []}
