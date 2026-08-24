from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient


def _job(client: TestClient) -> int:
    response = client.post(
        "/jobs/upsert",
        json={
            "linkedin_job_id": "v1-closeout-job",
            "title": "Senior Product Manager",
            "company": "Example, Inc.",
            "description": "Requires product strategy, roadmap leadership, and analytics.",
            "source": "linkedin",
        },
    )
    assert response.status_code == 200
    return int(response.json()["id"])


def test_version1_daily_use_flow(isolated_app: tuple[TestClient, Path]) -> None:
    client, _ = isolated_app
    job_id = _job(client)

    resume = client.post(
        "/resumes",
        json={
            "name": "Product Leadership",
            "version": "2026-08",
            "family": "Product Manager",
            "tags": ["strategy", "analytics", "roadmap"],
            "content_text": "Product strategy roadmap analytics leadership",
        },
    )
    assert resume.status_code == 201

    description = client.post(
        "/job-descriptions",
        json={
            "job_id": job_id,
            "source_type": "text",
            "raw_text": "Requires product strategy and analytics. Python is preferred.",
        },
    )
    assert description.status_code == 201
    assert description.json()["requirements"] == [
        "Requires product strategy and analytics. Python is preferred."
    ]
    assert "product" in description.json()["skills"]

    application = client.post(
        "/applications",
        json={"job_id": job_id, "resume_id": resume.json()["id"], "status": "applied"},
    )
    assert application.status_code == 201
    assert application.json()["company"] == "Example, Inc."
    assert application.json()["match_score"] > 0

    offer = client.post(
        "/offers",
        json={
            "application_id": application.json()["id"],
            "status": "received",
            "base_salary": 175000,
        },
    )
    assert offer.status_code == 201
    assert offer.json()["job_id"] == job_id

    companies = client.get("/companies").json()
    assert len(companies) == 1
    timeline = client.get(f"/companies/{companies[0]['id']}/timeline")
    assert timeline.status_code == 200
    assert {item["type"] for item in timeline.json()} >= {"application", "offer"}

    note = client.post(
        "/notes",
        json={
            "entity_type": "application",
            "entity_id": application.json()["id"],
            "body": "Follow up",
        },
    )
    assert note.status_code == 201
    assert (
        len(
            client.get(
                "/notes",
                params={"entity_type": "application", "entity_id": application.json()["id"]},
            ).json()
        )
        == 1
    )

    analytics = client.get("/analytics/version1").json()
    assert analytics["counts"] == {
        "applications": 1,
        "companies": 1,
        "resumes": 1,
        "offers": 1,
        "interactions": 2,
    }


def test_version1_is_additive_and_repeat_safe(isolated_app: tuple[TestClient, Path]) -> None:
    client, database = isolated_app
    job_id = _job(client)
    payload = {"job_id": job_id, "status": "applied"}

    assert client.post("/applications", json=payload).status_code == 201
    duplicate = client.post("/applications", json=payload)
    assert duplicate.status_code == 409

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM applications").fetchone() == (1,)


def test_explicit_linkedin_application_recording_is_repeat_safe(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = _job(client)

    first = client.post(
        f"/jobs/{job_id}/record-application", json={"applied_at": "2026-08-23T10:00:00"}
    )
    repeated = client.post(
        f"/jobs/{job_id}/record-application", json={"applied_at": "2026-08-23T10:01:00"}
    )

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["source"] == "linkedin_extension"
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["id"] == first.json()["id"]
    with sqlite3.connect(database) as connection:
        job = connection.execute(
            "SELECT status, applied_at, application_source FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert job == ("applied", "2026-08-23T10:00:00", "linkedin_extension")
        assert connection.execute("SELECT COUNT(*) FROM applications").fetchone() == (1,)


def test_explicit_linkedin_recording_respects_an_existing_application(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app
    job_id = _job(client)
    existing = client.post("/applications", json={"job_id": job_id, "status": "applied"})

    recorded = client.post(f"/jobs/{job_id}/record-application", json={})

    assert existing.status_code == 201
    assert recorded.status_code == 200
    assert recorded.json()["created"] is False
    assert recorded.json()["id"] == existing.json()["id"]


def test_extension_only_records_applications_after_an_explicit_user_action() -> None:
    root = Path(__file__).resolve().parents[1]
    popup = (root / "extension" / "popup.html").read_text()
    script = (root / "extension" / "popup.js").read_text()
    content = (root / "extension" / "content.js").read_text()

    assert "I applied — record selected job" in popup
    assert "RECORD_SELECTED_APPLICATION" in script
    assert "RECORD_SELECTED_APPLICATION" in content
    assert "/record-application" in content


def test_dashboard_and_api_performance_baseline(isolated_app: tuple[TestClient, Path]) -> None:
    client, _ = isolated_app
    started = time.perf_counter()
    responses = [
        client.get("/"),
        client.get("/applications"),
        client.get("/companies"),
        client.get("/offers"),
        client.get("/resumes"),
        client.get("/settings/status"),
    ]
    elapsed = time.perf_counter() - started

    assert all(response.status_code == 200 for response in responses)
    assert elapsed < 2.0
    assert all(
        label in responses[0].text
        for label in ("Applications", "Companies", "Offers", "Resumes", "Settings")
    )
