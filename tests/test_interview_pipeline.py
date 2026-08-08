from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from backend.app.services.email_classification import classify_email
from backend.app.services.interview_pipeline import EXTRACTOR_VERSION, extract_interview
from fastapi.testclient import TestClient

CASES = json.loads((Path(__file__).parent / "fixtures" / "interview" / "cases.json").read_text())


def mbox_message(
    case: dict[str, str], *, message_id: str, sender: str = "Avery Recruiter <avery@acme.com>"
) -> bytes:
    return (
        "From sender@example.com Sat Jan 01 00:00:00 2026\n"
        f"Subject: {case['subject']}\n"
        f"From: {sender}\n"
        "Date: Thu, 01 Jan 2026 12:00:00 +0000\n"
        f"Message-ID: <{message_id}>\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        f"{case['body']}\n\n"
    ).encode()


def upload(client: TestClient, content: bytes, mailbox: str = "gmail") -> dict[str, Any]:
    response = client.post(
        "/imports/mbox",
        data={"mailbox_name": mailbox},
        files={"file": ("interviews.mbox", content, "application/mbox")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_job(client: TestClient, job_id: str = "REQ-7000") -> int:
    response = client.post(
        "/jobs/upsert",
        json={"linkedin_job_id": job_id, "title": "Platform Engineer", "company": "Acme"},
    )
    assert response.status_code == 200
    return int(response.json()["id"])


def count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def case(name: str) -> dict[str, str]:
    return next(item for item in CASES if item["name"] == name)


def test_sanitized_fixtures_classify_and_extract_deterministically() -> None:
    for item in CASES:
        classification = classify_email(
            subject=item["subject"], sender="recruiter@acme.com", body=item["body"]
        )
        first = extract_interview(
            classification=classification.classification.value,
            subject=item["subject"],
            body=item["body"],
        )
        second = extract_interview(
            classification=classification.classification.value,
            subject=item["subject"],
            body=item["body"],
        )
        assert classification.classification.value == item["classification"]
        assert first == second
        assert first is not None
        assert first.interview_type == item["interview_type"]
        assert first.extractor_version == EXTRACTOR_VERSION


def test_timezone_duration_url_phone_and_location_extraction() -> None:
    technical = extract_interview(
        classification="INTERVIEW_INVITATION",
        subject=case("technical interview")["subject"],
        body=case("technical interview")["body"],
    )
    missing = extract_interview(
        classification="INTERVIEW_INVITATION",
        subject=case("missing timezone")["subject"],
        body=case("missing timezone")["body"],
    )
    assert technical is not None and missing is not None
    assert technical.scheduled_start == datetime(2027, 1, 22, 18, 30)
    assert technical.scheduled_end == datetime(2027, 1, 22, 19, 30)
    assert technical.meeting_url == "https://meet.google.com/tech-7000"
    assert missing.scheduled_start is None
    assert missing.local_start_text == "2027-01-26T14:00:00"
    assert missing.phone == "415-555-0199"
    assert missing.location_text == "100 Main Street"
    assert "UTC was not fabricated" in missing.ambiguity_reasons[0]


def test_malformed_schedule_is_preserved_as_unscheduled_evidence() -> None:
    evidence = extract_interview(
        classification="INTERVIEW_INVITATION",
        subject="Interview invitation",
        body="Your interview is scheduled for 2026-08-08 at 19:75 PM PST.",
    )

    assert evidence is not None
    assert evidence.scheduled_start is None
    assert evidence.local_start_text == ""
    assert "date or time could not be parsed; schedule omitted" in evidence.ambiguity_reasons


def test_invitation_confirmation_reschedule_and_cancellation_preserve_events(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    names = ("recruiter screen invitation", "interview confirmation", "reschedule", "cancellation")
    content = b"".join(
        mbox_message(case(name), message_id=f"event-{index}@example.com")
        for index, name in enumerate(names)
    )

    result = upload(client, content)
    interviews = client.get("/interviews").json()
    detail = client.get(f"/interviews/{interviews[0]['id']}").json()

    assert result["newly_imported"] == 4
    assert count(database, "jobs") == 1
    assert count(database, "interviews") == 1
    assert count(database, "interview_events") == 4
    assert interviews[0]["status"] == "cancelled"
    assert interviews[0]["scheduled_start"] == "2027-01-21T19:00:00"
    assert [item["event_type"] for item in detail["events"]] == [
        "invitation",
        "confirmation",
        "reschedule",
        "cancellation",
    ]
    assert detail["events"][0]["extractor_version"] == EXTRACTOR_VERSION


def test_recruiter_linkage_and_repeat_import_idempotency(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    recruiter = {
        "subject": "Reaching out about a role at Acme Inc.",
        "body": "Your background caught my attention. Job ID: REQ-7000. Senior Recruiter",
    }
    interview = mbox_message(
        case("recruiter screen invitation"), message_id="linked-interview@example.com"
    )
    upload(client, mbox_message(recruiter, message_id="recruiter@example.com") + interview)
    repeated = upload(client, interview)

    detail = client.get("/interviews").json()[0]
    assert count(database, "recruiters") == 1
    assert detail["recruiter_id"] is not None
    assert count(database, "interviews") == 1
    assert count(database, "interview_events") == 1
    assert repeated["already_imported"] == 1


def test_assessment_reminder_updates_one_assessment(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    content = mbox_message(case("assessment invitation"), message_id="assessment-1@example.com")
    content += mbox_message(case("assessment reminder"), message_id="assessment-2@example.com")
    upload(client, content)

    assert count(database, "interviews") == 1
    assert count(database, "interview_events") == 2
    assert (
        client.get("/interviews", params={"interview_type": "assessment"}).json()[0][
            "interview_type"
        ]
        == "assessment"
    )


def test_explicit_event_identifier_matches_without_schedule_or_url(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    invitation = {
        "subject": "Interview invitation",
        "body": "Schedule your interview. Job ID: REQ-7000. Event ID: EVT-7000",
    }
    confirmation = {
        "subject": "Interview confirmation",
        "body": "Your interview is confirmed. Job ID: REQ-7000. Event ID: EVT-7000",
    }
    upload(
        client,
        mbox_message(invitation, message_id="event-id-1@example.com")
        + mbox_message(confirmation, message_id="event-id-2@example.com"),
    )
    assert count(database, "interviews") == 1
    assert count(database, "interview_events") == 2


def test_unresolved_and_account_isolated_evidence_does_not_create_interview_or_job(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = create_job(client)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET email_account='hotmail' WHERE id=?", (job_id,))
        connection.commit()

    upload(client, mbox_message(case("unlinked interview"), message_id="unlinked@example.com"))
    upload(
        client,
        mbox_message(case("technical interview"), message_id="isolated@example.com"),
        mailbox="gmail",
    )

    assert count(database, "jobs") == 1
    assert count(database, "interviews") == 0
    assert count(database, "interview_events") == 2
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT interview_id, job_id, evidence_json FROM interview_events"
        ).fetchall()
    assert all(row[0] is None and row[1] is None for row in rows)
    assert all("no deterministic job linkage" in row[2] for row in rows)


def test_yahoo_raw_message_uses_same_interview_pipeline_without_duplicate_job(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    payload = {
        "records": [
            {
                "company": "Acme",
                "title": "Platform Engineer",
                "job_id": "REQ-7000",
                "confirmation_message_id": "yahoo-interview@example.com",
                "subject": case("panel interview")["subject"],
                "sender": "Avery Recruiter <avery@acme.com>",
                "body": case("panel interview")["body"],
            }
        ]
    }
    first = client.post("/imports/yahoo", json=payload)
    repeated = client.post("/imports/yahoo", json=payload)

    assert first.status_code == 200 and first.json()["newly_imported"] == 1
    assert repeated.status_code == 200 and repeated.json()["already_imported"] == 1
    assert count(database, "jobs") == 1
    assert count(database, "interviews") == 1
    assert count(database, "interview_events") == 1
    assert len(client.get("/interviews", params={"provider": "yahoo"}).json()) == 1


def test_read_only_api_filters_upcoming_detail_and_dashboard(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app
    job_id = create_job(client)
    upload(client, mbox_message(case("technical interview"), message_id="api@example.com"))

    listed = client.get(
        "/interviews",
        params={"job_id": job_id, "provider": "gmail", "upcoming": "true"},
    )
    upcoming = client.get("/interviews/upcoming")
    missing = client.get("/interviews/999999")
    dashboard = client.get("/").text

    assert listed.status_code == 200 and len(listed.json()) == 1
    assert upcoming.status_code == 200 and len(upcoming.json()) == 1
    assert missing.status_code == 404
    assert 'id="interviewsView"' in dashboard
    assert "No interview events have been imported yet." in dashboard
    assert "loadInterviews" in client.get("/static/app.js").text


def test_temporary_demo_never_touches_live_database() -> None:
    repository = Path(__file__).parents[1]
    live_database = repository / "data" / "jobs.db"
    live_existed = live_database.exists()
    before = hashlib.sha256(live_database.read_bytes()).hexdigest() if live_existed else None
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(live_database)
    result = subprocess.run(
        [sys.executable, "scripts/start_interview_demo.py", "--prepare-only"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "NON-PRODUCTION TEMPORARY INTERVIEW DEMO" in result.stdout
    assert "job-intelligence-interview-demo-" in result.stdout
    temporary_line = next(
        line for line in result.stdout.splitlines() if line.startswith("Temporary database: ")
    )
    temporary_database = Path(temporary_line.removeprefix("Temporary database: ")).resolve()
    assert not temporary_database.is_relative_to(repository.resolve())
    assert not temporary_database.exists()
    if live_existed:
        assert live_database.exists()
        assert before == hashlib.sha256(live_database.read_bytes()).hexdigest()
    else:
        assert not live_database.exists()


def test_interview_processing_failure_preserves_accepted_provenance(
    isolated_app: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, database = isolated_app
    create_job(client)

    def fail_processing(*args: object, **kwargs: object) -> None:
        raise ValueError("sanitized extractor failure")

    monkeypatch.setattr("backend.main.record_interview_evidence", fail_processing)
    result = upload(
        client,
        mbox_message(case("technical interview"), message_id="failure@example.com"),
    )

    assert result["newly_imported"] == 1
    assert count(database, "imported_messages") == 1
    assert count(database, "email_classifications") == 1
    assert count(database, "interview_events") == 0
    with sqlite3.connect(database) as connection:
        error = connection.execute("SELECT error FROM imported_messages").fetchone()[0]
    assert error == "interview processing failed: sanitized extractor failure"
