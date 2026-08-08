from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from backend.app.services.imap_checkpoint import ImapCheckpoint, write_checkpoint


def _provider(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in payload["providers"] if item["provider"] == name)


def test_sync_status_empty_state_is_read_only_and_provider_complete(
    isolated_app: tuple[Any, Path],
) -> None:
    client, database = isolated_app
    before = database.stat().st_mtime_ns

    response = client.get("/sync/status")

    assert response.status_code == 200
    payload = response.json()
    assert [item["provider"] for item in payload["providers"]] == [
        "gmail",
        "hotmail",
        "yahoo",
    ]
    assert {item["state"] for item in payload["providers"]} == {"never_synced"}
    assert payload["credentials_exposed"] is False
    assert payload["database_writes"] == 0
    assert database.stat().st_mtime_ns == before


def test_sync_status_hashes_account_scope_and_reports_checkpoint(
    isolated_app: tuple[Any, Path],
) -> None:
    client, database = isolated_app
    account = "private-user@example.invalid"
    now = datetime(2026, 8, 8, 12, tzinfo=UTC).replace(tzinfo=None)
    write_checkpoint(
        database,
        ImapCheckpoint(
            provider="gmail",
            account_namespace=account,
            folder="Jobs",
            since_date=date(2024, 7, 1),
            uidvalidity="700",
            last_successful_uid=55,
            sync_started_at=now,
            sync_completed_at=now,
            scanned_count=5,
            accepted_count=4,
            skipped_count=1,
            failure_count=0,
        ),
    )

    payload = client.get("/sync/status").json()
    gmail = _provider(payload, "gmail")
    serialized = str(payload)

    assert gmail["state"] == "checkpointed"
    assert gmail["checkpoint_count"] == 1
    assert gmail["scopes"][0]["last_successful_uid"] == 55
    assert len(gmail["scopes"][0]["account_reference"]) == 12
    assert account not in serialized


def test_dashboard_contains_provider_status_without_edit_controls(
    isolated_app: tuple[Any, Path],
) -> None:
    client, _ = isolated_app

    dashboard = client.get("/").text
    javascript = client.get("/static/app.js").text

    assert "Provider Synchronization" in dashboard
    assert "syncStatusRows" in dashboard
    assert "/sync/status" in javascript
    assert "sync now" not in dashboard.casefold()
