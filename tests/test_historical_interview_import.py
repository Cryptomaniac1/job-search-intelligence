from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.services.historical_interview_import import (
    HistoricalMessage,
    build_interview_candidate,
    iter_mbox_messages,
    iter_yahoo_messages,
)
from fastapi.testclient import TestClient


def message(
    *,
    provider: str = "gmail",
    message_id: str,
    subject: str,
    body: str,
    sender: str = "Avery Recruiter <avery@acme.example>",
    received_at: datetime | None = None,
) -> HistoricalMessage:
    return HistoricalMessage(
        provider=provider,
        source_name=f"{provider}-history",
        message_id=message_id,
        subject=subject,
        sender=sender,
        body=body,
        received_at=received_at or datetime(2027, 1, 1, 12),
    )


def run_import(messages: list[HistoricalMessage], source_name: str = "sanitized-history") -> dict:
    module = importlib.import_module("backend.main")
    result: dict = module.import_historical_interview_messages(
        iter(messages), source_name=source_name
    )
    return result


def create_job(
    client: TestClient,
    external_id: str,
    *,
    company: str = "Acme",
    account: str = "",
    database: Path | None = None,
) -> int:
    response = client.post(
        "/jobs/upsert",
        json={"linkedin_job_id": external_id, "title": "Platform Engineer", "company": company},
    )
    assert response.status_code == 200
    job_id = int(response.json()["id"])
    if account and database:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE jobs SET email_account=? WHERE id=?", (account, job_id))
    return job_id


def table_count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def job_rows(database: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(database) as connection:
        return list(connection.execute("SELECT * FROM jobs ORDER BY id"))


def lifecycle_messages() -> list[HistoricalMessage]:
    common = "Job ID: REQ-8000. Event ID: EVT-8000."
    return [
        message(
            message_id="invite@example.invalid",
            subject="Interview invitation",
            body=(
                f"Schedule your interview. {common} January 20, 2027 at 10:00 AM PST. "
                "https://zoom.us/j/8000\nSenior Recruiter"
            ),
            received_at=datetime(2027, 1, 2, 9),
        ),
        message(
            message_id="confirm@example.invalid",
            subject="Interview confirmation",
            body=(
                f"Your interview is confirmed. {common} January 20, 2027 at 10:00 AM PST. "
                "https://zoom.us/j/8000\nSenior Recruiter"
            ),
            received_at=datetime(2027, 1, 3, 9),
        ),
        message(
            message_id="reschedule@example.invalid",
            subject="Interview rescheduled",
            body=(
                f"Your interview rescheduled. {common} January 21, 2027 at 11:00 AM PST. "
                "https://zoom.us/j/8000\nSenior Recruiter"
            ),
            received_at=datetime(2027, 1, 4, 9),
        ),
        message(
            message_id="cancel@example.invalid",
            subject="Cancelled interview",
            body=f"Your interview has been cancelled. {common}\nSenior Recruiter",
            received_at=datetime(2027, 1, 5, 9),
        ),
    ]


def test_historical_lifecycle_preserves_provenance_job_rows_and_api_ordering(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = create_job(client, "REQ-8000", database=database)
    before_jobs = job_rows(database)
    messages = lifecycle_messages()
    messages.append(
        message(
            message_id="ambiguous@example.invalid",
            subject="Interview rescheduled and cancelled",
            body="Reschedule your interview, but this is also a cancelled interview.",
        )
    )

    result = run_import(messages)
    listed = client.get("/interviews").json()
    detail = client.get(f"/interviews/{listed[0]['id']}").json()

    assert result["total_messages"] == 5
    assert result["deterministic_candidates"] == 4
    assert result["ignored_messages"] == 1
    assert result["inserted_events"] == 4
    assert result["linked_events"] == 4
    assert job_rows(database) == before_jobs
    assert table_count(database, "imported_messages") == 4
    assert table_count(database, "email_classifications") == 4
    assert table_count(database, "interviews") == 1
    assert table_count(database, "interview_events") == 4
    assert table_count(database, "recruiters") == 1
    assert table_count(database, "recruiter_job_links") == 1
    assert listed[0]["job_id"] == job_id
    assert listed[0]["recruiter_id"] is not None
    assert listed[0]["status"] == "cancelled"
    assert [event["event_type"] for event in detail["events"]] == [
        "invitation",
        "confirmation",
        "reschedule",
        "cancellation",
    ]
    with sqlite3.connect(database) as connection:
        referenced = connection.execute(
            """SELECT COUNT(*)
               FROM interview_events event
               JOIN imported_messages message
                 ON message.stable_message_identity = event.source_message_identity
               JOIN email_classifications classification
                 ON classification.id = event.classification_id"""
        ).fetchone()
    assert referenced == (4,)


def test_repeated_historical_import_is_a_database_noop(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client, "REQ-8000", database=database)
    messages = lifecycle_messages()
    first = run_import(messages)
    with sqlite3.connect(database) as connection:
        before = {
            table: list(connection.execute(f"SELECT * FROM {table} ORDER BY id"))
            for table in (
                "email_imports",
                "imported_messages",
                "email_classifications",
                "recruiters",
                "recruiter_company_links",
                "recruiter_email_addresses",
                "recruiter_job_links",
                "interviews",
                "interview_events",
            )
        }

    second = run_import(messages)
    with sqlite3.connect(database) as connection:
        after = {
            table: list(connection.execute(f"SELECT * FROM {table} ORDER BY id"))
            for table in before
        }

    assert first["inserted_events"] == 4
    assert second["inserted_events"] == 0
    assert second["already_recorded"] == 4
    assert before == after


def test_existing_provenance_and_classification_are_reused_without_updates(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = create_job(client, "REQ-8050", database=database)
    historical = message(
        message_id="existing-provenance@example.invalid",
        subject="Interview invitation",
        body="Schedule your interview for Job ID: REQ-8050. Senior Recruiter",
    )
    candidate = build_interview_candidate(historical)
    assert candidate is not None
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            """INSERT INTO email_imports (
                   mailbox_name, source_filename, imported_at, total_messages,
                   confirmations_found, matched_jobs, unmatched_jobs
               ) VALUES ('gmail', 'legacy.mbox', '2027-01-01 12:00:00', 1, 0, 0, 0)"""
        )
        assert cursor.lastrowid is not None
        source_import_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO imported_messages (
                   provider, source_import_id, stable_message_identity, original_message_id,
                   imported_at, job_id, outcome, error
               ) VALUES (?, ?, ?, ?, '2027-01-01 12:00:00', ?, 'matched', '')""",
            (
                "gmail",
                source_import_id,
                candidate.identity,
                historical.message_id,
                job_id,
            ),
        )
        connection.execute(
            """INSERT INTO email_classifications (
                   message_identity, job_id, classification, confidence,
                   classifier_version, reason_json, created_at
               ) VALUES (?, ?, 'INTERVIEW_INVITATION', 0.98,
                         'deterministic-v1', '[\"legacy evidence\"]',
                         '2027-01-01 12:00:00')""",
            (candidate.identity, job_id),
        )
        connection.commit()
        before_message = connection.execute("SELECT * FROM imported_messages").fetchone()
        before_classification = connection.execute("SELECT * FROM email_classifications").fetchone()

    result = run_import([historical])

    with sqlite3.connect(database) as connection:
        after_message = connection.execute("SELECT * FROM imported_messages").fetchone()
        after_classification = connection.execute("SELECT * FROM email_classifications").fetchone()
    assert result["created_provenance"] == 0
    assert result["created_classifications"] == 0
    assert result["inserted_events"] == 1
    assert before_message == after_message
    assert before_classification == after_classification
    assert table_count(database, "email_imports") == 1
    assert table_count(database, "interview_events") == 1


def test_assessments_timezone_handling_and_unmatched_evidence(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client, "REQ-8100", database=database)
    messages = [
        message(
            message_id="assessment-invite@example.invalid",
            subject="Assessment invitation",
            body=(
                "Please complete an assessment for Job ID: REQ-8100 by January 25, 2027 at "
                "5:00 PM UTC. https://www.hackerrank.com/test/8100\nSenior Recruiter"
            ),
        ),
        message(
            message_id="assessment-reminder@example.invalid",
            subject="Assessment reminder",
            body=(
                "Reminder to complete the assessment for Job ID: REQ-8100 by January 25, 2027 "
                "at 5:00 PM UTC. https://www.hackerrank.com/test/8100\nSenior Recruiter"
            ),
        ),
        message(
            message_id="missing-timezone@example.invalid",
            subject="Interview invitation",
            body="Schedule your interview for Job ID: REQ-8100 on January 26, 2027 at 2:00 PM.",
        ),
        message(
            message_id="unmatched@example.invalid",
            subject="Interview invitation",
            body="Schedule your interview on January 27, 2027 at 3:00 PM PDT.",
        ),
    ]

    result = run_import(messages)

    assert result["inserted_events"] == 4
    assert result["unmatched_events"] == 1
    assert len(client.get("/interviews", params={"interview_type": "assessment"}).json()) == 1
    with sqlite3.connect(database) as connection:
        missing = connection.execute(
            """SELECT extracted_start, timezone, evidence_json
               FROM interview_events WHERE source_message_identity = (
                   SELECT stable_message_identity FROM imported_messages
                   WHERE original_message_id = 'missing-timezone@example.invalid'
               )"""
        ).fetchone()
        unmatched = connection.execute(
            """SELECT interview_id, job_id FROM interview_events
               WHERE source_message_identity = (
                   SELECT stable_message_identity FROM imported_messages
                   WHERE original_message_id = 'unmatched@example.invalid'
               )"""
        ).fetchone()
    assert missing is not None and missing[0] is None and missing[1] is None
    assert "UTC was not fabricated" in missing[2]
    assert unmatched == (None, None)


def test_distinct_interviews_are_not_merged_from_recruiter_and_type_alone(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client, "REQ-8150", database=database)
    messages = [
        message(
            message_id=f"distinct-{event_id}@example.invalid",
            subject="Interview invitation",
            body=(
                f"Schedule your interview for Job ID: REQ-8150. Event ID: {event_id}. "
                "Senior Recruiter"
            ),
        )
        for event_id in ("EVT-FIRST", "EVT-SECOND")
    ]

    run_import(messages)

    assert table_count(database, "interview_events") == 2
    assert table_count(database, "interviews") == 2


def test_provider_scoping_and_account_isolation(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    gmail_job = create_job(client, "REQ-GMAIL", account="gmail", company="Acme", database=database)
    hotmail_job = create_job(
        client, "REQ-HOTMAIL", account="hotmail", company="Beta", database=database
    )
    shared_id = "shared-provider-message@example.invalid"
    gmail = message(
        provider="gmail",
        message_id=shared_id,
        subject="Interview invitation",
        body="Schedule your interview for Job ID: REQ-GMAIL. Senior Recruiter",
    )
    hotmail = message(
        provider="hotmail",
        message_id=shared_id,
        subject="Interview invitation",
        sender="Blair Recruiter <blair@beta.example>",
        body="Schedule your interview for Job ID: REQ-HOTMAIL. Senior Recruiter",
    )
    wrong_account = message(
        provider="gmail",
        message_id="wrong-account@example.invalid",
        subject="Interview invitation",
        body="Schedule your interview for Job ID: REQ-HOTMAIL. Senior Recruiter",
    )

    run_import([gmail], "gmail.mbox")
    run_import([hotmail], "hotmail.mbox")
    run_import([wrong_account], "gmail-wrong-account.mbox")

    with sqlite3.connect(database) as connection:
        rows = list(
            connection.execute(
                "SELECT provider, original_message_id, job_id FROM imported_messages ORDER BY id"
            )
        )
    assert rows[0] == ("gmail", shared_id, gmail_job)
    assert rows[1] == ("hotmail", shared_id, hotmail_job)
    assert rows[2] == ("gmail", "wrong-account@example.invalid", None)
    assert table_count(database, "interview_events") == 3
    assert table_count(database, "interviews") == 2


def test_mbox_and_yahoo_archives_use_the_same_candidate_pipeline(tmp_path: Path) -> None:
    mbox_path = tmp_path / "gmail.mbox"
    mbox_path.write_text(
        "From sender@example.com Sat Jan 01 00:00:00 2027\n"
        "Subject: Interview invitation\n"
        "From: Avery Recruiter <avery@acme.example>\n"
        "Date: Fri, 01 Jan 2027 12:00:00 +0000\n"
        "Message-ID: <provider-case@example.invalid>\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "Schedule your interview for Job ID: REQ-8200.\n\n",
        encoding="utf-8",
    )
    yahoo_path = tmp_path / "yahoo.json"
    yahoo_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "confirmation_message_id": "provider-case@example.invalid",
                        "subject": "Interview invitation",
                        "sender": "Avery Recruiter <avery@acme.example>",
                        "body": "Schedule your interview for Job ID: REQ-8200.",
                        "received_at": "2027-01-01T12:00:00Z",
                    },
                    {"company": "Legacy structured record", "job_id": "ignored"},
                ]
            }
        ),
        encoding="utf-8",
    )

    gmail_candidate = build_interview_candidate(next(iter_mbox_messages(mbox_path, "gmail")))
    yahoo_messages = list(iter_yahoo_messages(yahoo_path))
    yahoo_candidate = build_interview_candidate(yahoo_messages[0])

    assert gmail_candidate is not None and yahoo_candidate is not None
    assert gmail_candidate.evidence == yahoo_candidate.evidence
    assert gmail_candidate.identity != yahoo_candidate.identity
    assert len(yahoo_messages) == 1


def test_populated_dashboard_filters_upcoming_and_detail_api(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client, "REQ-8000", database=database)
    create_job(client, "REQ-8100", database=database)
    run_import(lifecycle_messages())
    run_import(
        [
            message(
                message_id="assessment-api@example.invalid",
                subject="Assessment invitation",
                body=(
                    "Please complete an assessment for Job ID: REQ-8100 on January 25, 2027 at "
                    "5:00 PM UTC. https://www.hackerrank.com/test/api\nSenior Recruiter"
                ),
            )
        ],
        "assessment-history",
    )

    all_interviews = client.get("/interviews")
    upcoming = client.get("/interviews/upcoming")
    assessments = client.get("/interviews", params={"interview_type": "assessment"})
    detail = client.get(f"/interviews/{assessments.json()[0]['id']}")
    dashboard = client.get("/")
    javascript = client.get("/static/app.js")

    assert all_interviews.status_code == 200 and len(all_interviews.json()) == 2
    assert upcoming.status_code == 200 and len(upcoming.json()) == 1
    assert assessments.status_code == 200 and len(assessments.json()) == 1
    assert detail.status_code == 200 and detail.json()["events"][0]["event_type"] == (
        "assessment_invitation"
    )
    assert dashboard.status_code == 200 and 'id="interviewsView"' in dashboard.text
    assert javascript.status_code == 200
    assert "d.length?d.map" in javascript.text
    assert "upcomingInterviewTotal" in javascript.text


def test_operator_cli_uses_explicit_database_and_refuses_runtime_path(
    isolated_app: tuple[TestClient, Path], tmp_path: Path
) -> None:
    client, database = isolated_app
    create_job(client, "REQ-8300", database=database)
    archive = tmp_path / "historical-cli.mbox"
    archive.write_text(
        "From sender@example.com Sat Jan 01 00:00:00 2027\n"
        "Subject: Interview invitation\n"
        "From: Avery Recruiter <avery@acme.example>\n"
        "Date: Fri, 01 Jan 2027 12:00:00 +0000\n"
        "Message-ID: <cli-case@example.invalid>\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "Schedule your interview for Job ID: REQ-8300. Senior Recruiter\n\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "scripts/import_historical_interviews.py",
        "--database",
        str(database),
        "--gmail-mbox",
        str(archive),
    ]

    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    protected = subprocess.run(
        [
            *command[:3],
            str(Path(__file__).parents[1] / "data" / "jobs.db"),
            *command[4:],
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    result = json.loads(completed.stdout)
    assert result["sources"][0]["inserted_events"] == 1
    assert table_count(database, "interview_events") == 1
    assert protected.returncode != 0
    assert "Refusing protected runtime database" in protected.stderr
