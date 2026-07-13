from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.app.services.recruiter_crm import extract_recruiter, normalize_company
from fastapi.testclient import TestClient

EXTRACTION_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "recruiter" / "extraction_cases.json").read_text()
)


def recruiter_message(
    *,
    message_id: str,
    sender: str = "Jane Smith <jane@acme.com>",
    company: str = "Acme, Inc.",
    job_id: str = "REQ-6000",
    date: str = "Thu, 01 Jan 2026 12:00:00 +0000",
) -> bytes:
    return (
        "From sender@example.com Sat Jan 01 00:00:00 2026\n"
        f"Subject: Reaching out about a role at {company}\n"
        f"From: {sender}\n"
        f"Date: {date}\n"
        f"Message-ID: <{message_id}>\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"Your background caught my attention. Job ID: {job_id}.\n"
        "Best,\nJane Smith\nSenior Recruiter\n"
        "https://www.linkedin.com/in/jane-smith\n"
        "415-555-0100\n\n"
    ).encode()


def upload(client: TestClient, content: bytes) -> dict[str, Any]:
    response = client.post(
        "/imports/mbox",
        data={"mailbox_name": "gmail"},
        files={"file": ("recruiter.mbox", content, "application/mbox")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def create_job(client: TestClient, job_id: str = "REQ-6000") -> int:
    response = client.post(
        "/jobs/upsert",
        json={"linkedin_job_id": job_id, "title": "Product Manager", "company": "Acme"},
    )
    assert response.status_code == 200
    return int(response.json()["id"])


def test_extraction_fixtures_are_deterministic() -> None:
    for case in EXTRACTION_CASES:
        first = extract_recruiter(
            classification=case["classification"],
            sender=case["sender"],
            subject=case["subject"],
            body=case["body"],
        )
        second = extract_recruiter(
            classification=case["classification"],
            sender=case["sender"],
            subject=case["subject"],
            body=case["body"],
        )
        assert first == second
        assert first is not None
        assert first.normalized_email == case["expected_email"]
        assert first.normalized_company == case["expected_company"]


def test_extraction_normalizes_legal_suffixes() -> None:
    evidence = extract_recruiter(
        classification="RECRUITER_OUTREACH",
        sender="Jane Smith <Jane.Smith@google.com>",
        subject="A role at Google LLC",
        body="Your background caught my attention.\nSenior Recruiter",
    )

    assert evidence is not None
    assert evidence.normalized_email == "jane.smith@google.com"
    assert evidence.normalized_company == "google"
    assert evidence.confidence == 0.95
    assert normalize_company("Google, Inc.") == normalize_company("Google LLC")


def test_new_recruiter_and_explicit_job_link_are_created(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = create_job(client)

    result = upload(client, recruiter_message(message_id="recruiter-1@example.com"))
    recruiters = client.get("/recruiters", params={"company": "Acme LLC"}).json()

    assert result["newly_imported"] == 1
    assert count(database, "recruiters") == 1
    assert count(database, "recruiter_email_addresses") == 1
    assert count(database, "recruiter_company_links") == 1
    assert count(database, "recruiter_job_links") == 1
    assert recruiters[0]["name"] == "Jane Smith"
    assert recruiters[0]["job_links"][0]["job_id"] == job_id


def test_repeated_observations_do_not_duplicate_and_preserve_first_source(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client)
    first_identity = "recruiter-first@example.com"
    upload(client, recruiter_message(message_id=first_identity))
    upload(
        client,
        recruiter_message(
            message_id="recruiter-second@example.com",
            date="Fri, 02 Jan 2026 12:00:00 +0000",
        ),
    )
    upload(
        client,
        recruiter_message(
            message_id="recruiter-second@example.com",
            date="Fri, 02 Jan 2026 12:00:00 +0000",
        ),
    )

    assert count(database, "recruiters") == 1
    assert count(database, "recruiter_job_links") == 1
    with sqlite3.connect(database) as connection:
        expected_identity = connection.execute(
            """SELECT stable_message_identity FROM imported_messages
               WHERE original_message_id LIKE ?""",
            (f"%{first_identity}%",),
        ).fetchone()[0]
        row = connection.execute(
            """SELECT source_message_identity, first_seen_at, last_seen_at
               FROM recruiter_job_links"""
        ).fetchone()
    assert row is not None
    assert row[0] == expected_identity
    assert row[1] < row[2]


def test_company_match_alone_does_not_create_job_link(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    create_job(client, "OTHER-1000")

    upload(client, recruiter_message(message_id="no-explicit-job@example.com"))

    assert count(database, "recruiters") == 1
    assert count(database, "recruiter_job_links") == 0


def test_same_email_at_different_companies_does_not_merge(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    upload(
        client,
        recruiter_message(
            message_id="agency-acme@example.com",
            sender="Alex Agent <alex@agency.com>",
            company="Acme Inc.",
        ),
    )
    upload(
        client,
        recruiter_message(
            message_id="agency-beta@example.com",
            sender="Alex Agent <alex@agency.com>",
            company="Beta LLC",
        ),
    )

    assert count(database, "recruiters") == 2
    assert len(client.get("/recruiters", params={"email": "alex@agency.com"}).json()) == 2


def test_signature_matches_within_company_when_sender_name_is_missing(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    upload(
        client,
        recruiter_message(
            message_id="signature-one@example.com",
            sender="jane@acme.com",
        ),
    )
    upload(
        client,
        recruiter_message(
            message_id="signature-two@example.com",
            sender="jane.smith@acme.com",
        ),
    )

    assert count(database, "recruiters") == 1
    assert count(database, "recruiter_email_addresses") == 2


def test_multiple_recruiters_and_read_only_detail_api(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    upload(client, recruiter_message(message_id="jane@example.com"))
    upload(
        client,
        recruiter_message(
            message_id="john@example.com",
            sender="John Doe <john@acme.com>",
        ),
    )

    recruiters = client.get("/recruiters").json()
    detail = client.get(f"/recruiters/{recruiters[0]['id']}")

    assert count(database, "recruiters") == 2
    assert detail.status_code == 200
    assert detail.json()["contact_count"] == 1
    assert client.get("/recruiters/999999").status_code == 404
    assert 'id="recruiterDetail"' in client.get("/").text


def test_insufficient_sender_evidence_does_not_create_recruiter(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    upload(
        client,
        recruiter_message(
            message_id="generic@example.com",
            sender="Careers <careers@gmail.com>",
            company="",
        ),
    )

    assert count(database, "recruiters") == 0
