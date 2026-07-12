from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def test_application_starts_and_health_is_compatible(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "2.0.0"}


def test_job_upsert_list_and_status_update(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app
    payload = {
        "linkedin_job_id": "test-123",
        "title": "Product Manager",
        "company": "Example Corp",
        "location": "Remote",
        "applicant_count": 20,
        "source": "linkedin",
    }

    created = client.post("/jobs/upsert", json=payload)
    assert created.status_code == 200
    assert created.json()["linkedin_job_id"] == "test-123"
    assert created.json()["score"] == 91.0

    listed = client.get("/jobs", params={"search": "Example"})
    assert listed.status_code == 200
    assert [job["linkedin_job_id"] for job in listed.json()] == ["test-123"]

    updated = client.patch(
        f"/jobs/{created.json()['id']}/status",
        json={"status": "saved", "notes": "Regression test"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "saved"
    assert updated.json()["notes"] == "Regression test"


def test_dashboard_and_static_assets_are_available(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, _ = isolated_app

    dashboard = client.get("/")
    javascript = client.get("/static/app.js")

    assert dashboard.status_code == 200
    assert "Job Intelligence" in dashboard.text
    assert javascript.status_code == 200
    assert "loadHome" in javascript.text


def test_database_is_created_only_at_configured_temporary_path(
    isolated_app: tuple[TestClient, Path],
) -> None:
    _, database_path = isolated_app

    assert database_path.exists()
    assert database_path.name == "test-jobs.db"
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"jobs", "email_imports"}.issubset(tables)
