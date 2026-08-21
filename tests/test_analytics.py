from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient


def _job(client: TestClient, identity: str, company: str, role: str) -> int:
    response = client.post(
        "/jobs/upsert",
        json={
            "linkedin_job_id": identity,
            "title": role,
            "company": company,
            "location": "Remote",
            "source": "test",
        },
    )
    assert response.status_code == 200
    return int(response.json()["id"])


def _set_job(
    database: Path,
    job_id: int,
    *,
    status: str,
    applied_at: str | None,
    first_seen_at: str,
    role: str,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE jobs
                  SET status=?, applied_at=?, first_seen_at=?, last_seen_at=?, role_family=?
                WHERE id=?""",
            (status, applied_at, first_seen_at, first_seen_at, role, job_id),
        )
        connection.commit()


def _evidence(
    database: Path,
    job_id: int | None,
    identity: str,
    classification: str,
    received_at: str,
) -> None:
    with sqlite3.connect(database) as connection:
        import_id = connection.execute(
            """INSERT INTO email_imports (
                   mailbox_name, source_filename, imported_at, total_messages,
                   confirmations_found, matched_jobs, unmatched_jobs
               ) VALUES ('test', 'fixture', ?, 1, 0, 0, 0)""",
            (received_at,),
        ).lastrowid
        connection.execute(
            """INSERT INTO imported_messages (
                   provider, source_import_id, stable_message_identity,
                   original_message_id, imported_at, job_id, outcome, error
               ) VALUES ('test', ?, ?, '', ?, ?, 'accepted', '')""",
            (import_id, identity, received_at, job_id),
        )
        connection.execute(
            """INSERT INTO email_classifications (
                   message_identity, job_id, classification, confidence,
                   classifier_version, reason_json, created_at
               ) VALUES (?, ?, ?, 0.99, 'test-v1', '[]', ?)""",
            (identity, job_id, classification, received_at),
        )
        connection.execute(
            """INSERT INTO imap_message_metadata (
                   message_identity, provider, account_namespace, folder,
                   uidvalidity, imap_uid, subject, sender, received_at,
                   imap_internal_date, requested_since_date, text_body,
                   html_fallback_used, recipients_json, attachments_json, created_at
               ) VALUES (?, 'test', 'test', 'jobs', '1', ?, '', '', ?, ?,
                         '2026-01-01', '', 0, '[]', '[]', ?)""",
            (identity, int(identity[-2:], 16) + 1, received_at, received_at, received_at),
        )
        connection.commit()


def test_corrected_analytics_separates_imports_and_deduplicates_linked_evidence(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    first = _job(client, "analytics-1", "Example Corp", "Product")
    second = _job(client, "analytics-2", "example corp", "Product")
    duplicate = _job(client, "analytics-duplicate", "Example Corp", "Product")
    saved = _job(client, "analytics-saved", "Example Corp", "Product")
    _set_job(
        database,
        first,
        status="interview",
        applied_at="2026-07-15 12:00:00",
        first_seen_at="2026-08-01 12:00:00",
        role="Product",
    )
    _set_job(
        database,
        second,
        status="interview",
        applied_at=None,
        first_seen_at="2026-08-02 12:00:00",
        role="Product",
    )
    _set_job(
        database,
        duplicate,
        status="interview",
        applied_at="2026-07-15 12:00:00",
        first_seen_at="2026-08-01 12:00:00",
        role="Product",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE jobs SET source='email', email_account='hotmail',
                      confirmation_message_id='same-message'
                  WHERE id IN (?, ?)""",
            (first, duplicate),
        )
        connection.commit()
    _set_job(
        database,
        saved,
        status="saved",
        applied_at="2026-07-16 12:00:00",
        first_seen_at="2026-07-16 12:00:00",
        role="Product",
    )
    _evidence(database, first, "a" * 65 + "01", "INTERVIEW_INVITATION", "2026-07-20")
    _evidence(database, first, "a" * 65 + "02", "INTERVIEW_CONFIRMATION", "2026-07-21")
    _evidence(database, None, "a" * 65 + "03", "INTERVIEW_INVITATION", "2026-07-22")

    overview = client.get("/analytics/overview").json()
    assert overview["all_time"]["applications"] == 2
    assert overview["all_time"]["recruiter_replies"] == 1
    assert overview["all_time"]["interviews"] == 1
    assert overview["data_quality"]["raw_application_stage_rows"] == 3
    assert overview["data_quality"]["canonical_applications"] == 2
    assert overview["data_quality"]["collapsed_duplicate_or_overlapping_rows"] == 1
    assert overview["data_quality"]["undated_applications"] == 1
    assert overview["data_quality"]["outcome_evidence"]["interviews"]["unlinked_records"] == 1

    timeline = {row["period"]: row for row in client.get("/analytics/timeline").json()}
    assert timeline["2026-07"]["applications"] == 1
    assert timeline["2026-08"]["applications"] == 0
    assert timeline["2026-08"]["records_imported"] == 1

    roles = client.get("/analytics/roles").json()
    assert roles[0]["applications"] == 2
    assert roles[0]["interviews"] == 1
    assert roles[0]["interview_rate"] == 50.0
    companies = client.get("/analytics/companies").json()
    assert len(companies) == 1
    assert companies[0]["company"] == "Example Corp"
    assert companies[0]["last_activity"].startswith("2026-07-21")


def test_period_comparison_uses_real_application_dates_and_handles_zero_baseline(
    isolated_app: tuple[TestClient, Path],
) -> None:
    client, database = isolated_app
    job_id = _job(client, "analytics-recent", "Recent Co", "Marketing")
    _set_job(
        database,
        job_id,
        status="applied",
        applied_at="2026-08-01 00:00:00",
        first_seen_at="2026-08-08 00:00:00",
        role="Marketing",
    )
    from backend.app.services.analytics import analytics_overview

    overview = analytics_overview(database, now=datetime(2026, 8, 8))
    assert overview["last_30_days"]["applications"] == 1
    assert overview["rolling_windows"]["last_60_days"]["applications"] == 1
    assert overview["rolling_windows"]["last_90_days"]["applications"] == 1
    assert overview["previous_90_monthly_average"]["applications"] == 0.0
    assert overview["change_vs_previous_90_monthly_average_percent"]["applications"] is None
    assert overview["change_vs_previous_90_monthly_average_percent"]["offers"] == 0.0
    august = overview["monthly_activity"][-1]
    assert august["period"] == "2026-08"
    assert august["applications"] == 1
    assert august["is_month_to_date"] is True
    assert august["comparison_basis"] == "previous_month_same_elapsed_days"
