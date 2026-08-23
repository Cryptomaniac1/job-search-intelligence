from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def mbox_message(
    *,
    message_id: str | None = "message-1@example.com",
    subject: str = "Application confirmation",
    body: str = "Thank you for applying. Position: Product Manager",
) -> bytes:
    message_id_header = f"Message-ID: <{message_id}>\n" if message_id else ""
    return (
        "From sender@example.com Sat Jan 01 00:00:00 2026\n"
        f"Subject: {subject}\n"
        "From: careers@example.com\n"
        "Date: Thu, 01 Jan 2026 12:00:00 +0000\n"
        f"{message_id_header}"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        f"{body}\n"
        "\n"
    ).encode()


def upload_mbox(
    client: TestClient, provider: str, content: bytes, account_namespace: str = ""
) -> dict[str, Any]:
    response = client.post(
        "/imports/mbox",
        data={"mailbox_name": provider, "account_namespace": account_namespace},
        files={"file": (f"{provider}.mbox", content, "application/mbox")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def table_count(database_path: Path, table: str) -> int:
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert result is not None
    return int(result[0])


def test_repeated_mbox_import_is_idempotent_and_auditable(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message()

    first = upload_mbox(client, "gmail", content)
    second = upload_mbox(client, "gmail", content)

    assert first["newly_imported"] == 1
    assert first["already_imported"] == 0
    assert second["newly_imported"] == 0
    assert second["already_imported"] == 1
    assert table_count(database_path, "jobs") == 1
    assert table_count(database_path, "imported_messages") == 1
    assert table_count(database_path, "email_classifications") == 1
    assert table_count(database_path, "email_imports") == 2


def test_missing_message_id_import_is_repeatable(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(message_id=None)

    first = upload_mbox(client, "hotmail", content)
    second = upload_mbox(client, "hotmail", content)

    assert first["newly_imported"] == 1
    assert second["already_imported"] == 1
    assert table_count(database_path, "jobs") == 1


def test_accounts_remain_separate_for_the_same_message_id(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(message_id="shared@example.com")

    gmail = upload_mbox(client, "gmail", content)
    hotmail = upload_mbox(client, "hotmail", content)

    assert gmail["newly_imported"] == 1
    assert hotmail["newly_imported"] == 1
    assert table_count(database_path, "jobs") == 2
    assert table_count(database_path, "email_classifications") == 2
    with sqlite3.connect(database_path) as connection:
        accounts = {row[0] for row in connection.execute("SELECT email_account FROM jobs")}
    assert accounts == {"gmail", "hotmail"}


def test_same_provider_archives_remain_account_scoped(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(message_id="shared@gmail.example")

    pm = upload_mbox(client, "gmail", content, "solovat@gmail.com")
    marketing = upload_mbox(client, "gmail", content, "soultanovr@gmail.com")

    assert pm["role_family"] == "Product Manager / Technical Program Manager"
    assert marketing["role_family"] == "Marketing"
    assert table_count(database_path, "imported_messages") == 2
    with sqlite3.connect(database_path) as connection:
        accounts = {row[0] for row in connection.execute("SELECT email_account FROM jobs")}
    assert accounts == {"solovat@gmail.com", "soultanovr@gmail.com"}


def test_ibuildanapp_import_uses_explicit_role_not_account_default(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(
        subject="Application received",
        body=(
            "Thank you for applying for the position of Technical Delivery Manager. "
            "Our team will review your application."
        ),
    )

    upload_mbox(client, "gmail", content, "ibuildanapp@gmail.com")

    with sqlite3.connect(database_path) as connection:
        title, role = connection.execute("SELECT title, role_family FROM jobs").fetchone()
    assert title == "Technical Delivery Manager"
    assert role == "Delivery Management"


def test_ibuildanapp_roles_are_deterministically_separated(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    for index, title in enumerate(
        ["Senior Sales Engineer", "Solutions Consultant", "Operations Manager"]
    ):
        upload_mbox(
            client,
            "gmail",
            mbox_message(
                message_id=f"role-{index}@example.com",
                body=f"Thank you for applying. Position: {title}.",
            ),
            "ibuildanapp@gmail.com",
        )

    with sqlite3.connect(database_path) as connection:
        roles = {row[0] for row in connection.execute("SELECT role_family FROM jobs")}
    assert roles == {"Sales Engineering", "Solutions Consulting", "Operations Management"}


def test_cross_account_generic_extracted_id_does_not_collide(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(
        message_id="generic-id@example.com",
        body="Thank you for applying. Job Application received.",
    )

    upload_mbox(client, "gmail", content, "solovat@gmail.com")
    upload_mbox(client, "gmail", content, "soultanovr@gmail.com")

    assert table_count(database_path, "jobs") == 2


def test_yahoo_repeat_import_and_provenance(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    payload = {
        "records": [
            {
                "company": "Example",
                "title": "Technical Program Manager",
                "applied_at": "2026-01-01T12:00:00",
                "job_id": "yahoo-job-1",
                "confirmation_message_id": "yahoo-message-1@example.com",
            }
        ]
    }

    first = client.post("/imports/yahoo", json=payload)
    second = client.post("/imports/yahoo", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["newly_imported"] == 1
    assert second.json()["already_imported"] == 1
    assert table_count(database_path, "jobs") == 1
    with sqlite3.connect(database_path) as connection:
        provenance = connection.execute(
            """SELECT provider, original_message_id, outcome, job_id
               FROM imported_messages"""
        ).fetchone()
    assert provenance == ("yahoo", "yahoo-message-1@example.com", "unmatched", 1)
    assert table_count(database_path, "email_classifications") == 1


def test_matched_import_preserves_stronger_historical_fields(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app
    created = client.post(
        "/jobs/upsert",
        json={
            "linkedin_job_id": "REQ-1234",
            "title": "Principal Product Manager",
            "company": "Strong Company",
            "source": "linkedin",
        },
    ).json()
    client.patch(
        f"/jobs/{created['id']}/status",
        json={"status": "saved", "notes": "Keep this history"},
    )
    content = mbox_message(
        message_id="matched@example.com",
        body="Thank you for applying. Job ID: REQ-1234. Position: Product Manager",
    )

    result = upload_mbox(client, "gmail", content)
    preserved = client.get("/jobs", params={"search": "Strong Company"}).json()[0]

    assert result["matched"] == 1
    assert preserved["status"] == "saved"
    assert preserved["source"] == "linkedin"
    assert preserved["company"] == "Strong Company"
    assert preserved["title"] == "Principal Product Manager"
    assert preserved["notes"] == "Keep this history"
    assert preserved["email_account"] == "gmail"


def test_blank_message_is_preserved_as_unknown_evidence_without_a_job(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app

    result = upload_mbox(client, "gmail", mbox_message(subject="", body=""))

    assert result["newly_imported"] == 1
    assert result["failed"] == 0
    assert table_count(database_path, "jobs") == 0
    assert table_count(database_path, "imported_messages") == 1
    assert table_count(database_path, "email_classifications") == 1


def test_non_confirmation_is_classified_without_creating_a_job(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    content = mbox_message(
        message_id="interview@example.com",
        subject="Interview invitation",
        body="Please schedule your interview.",
    )

    result = upload_mbox(client, "gmail", content)
    classifications = client.get(
        "/email-classifications",
        params={"classification": "interview_invitation", "provider": "gmail"},
    ).json()

    assert result["newly_imported"] == 1
    assert result["confirmations_found"] == 0
    assert table_count(database_path, "jobs") == 0
    assert classifications[0]["classification"] == "INTERVIEW_INVITATION"
    assert classifications[0]["classifier_version"] == "deterministic-v1"
    assert classifications[0]["reasons"]


def test_imported_job_cannot_be_hard_deleted(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    imported = upload_mbox(client, "gmail", mbox_message())
    job_id = imported["preview"][0]["matched_job_id"]

    response = client.delete(f"/jobs/{job_id}")

    assert response.status_code == 409
    assert table_count(database_path, "jobs") == 1


def test_non_imported_job_keeps_existing_delete_contract(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database_path = isolated_app
    job = client.post(
        "/jobs/upsert",
        json={"linkedin_job_id": "delete-me", "title": "Temporary"},
    ).json()

    response = client.delete(f"/jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert table_count(database_path, "jobs") == 0
