from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from backend.app.services.yahoo_imap import YahooImapSettings
from backend.app.services.yahoo_live_sync import (
    FIRST_LIVE_LIMIT,
    FIRST_LIVE_UID,
    LIVE_CONFIRMATION_TOKEN,
    authorize_first_live_batch,
    preflight_live_sync,
)
from scripts.sync_yahoo_imap import write_json_evidence

ROOT = Path(__file__).resolve().parents[1]
SINCE_DATE = date(2024, 7, 1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings() -> YahooImapSettings:
    return YahooImapSettings(
        "person@yahoo.com", "synthetic-app-password", "job", "imap.mail.yahoo.com", 993
    )


@pytest.fixture()
def live_gate_files(isolated_app: tuple[Any, Path], tmp_path: Path) -> tuple[Path, Path, Path]:
    _, database = isolated_app
    backup = tmp_path / "verified-backup.sqlite3"
    shutil.copy2(database, backup)
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(backup)
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "20260712_0005"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = tmp_path / "verified-backup.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "path": str(backup),
                "checksum_sha256": _sha256(backup),
                "alembic_revision": "20260712_0005",
                "integrity_check": ["ok"],
                "foreign_key_violations": [],
            }
        )
    )
    dry_run = tmp_path / "approved-dry-run.json"
    dry_run.write_text(
        json.dumps(
            {
                "folder": "job",
                "since_date": "2024-07-01",
                "requested_start_uid": 53290,
                "search_complete": True,
                "total_matched_uid_count": 1000,
                "batch_selected_count": 100,
                "processed_count": 100,
                "completed_count": 100,
                "accepted_candidates": 100,
                "failure_count": 0,
                "database_writes": 0,
                "mailbox_mutations": 0,
                "uidvalidity": "1578947209",
            }
        )
    )
    return database, metadata, dry_run


def _preflight(
    database: Path, metadata: Path, dry_run: Path, **overrides: object
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "folder": "job",
        "since_date": SINCE_DATE,
        "backup_metadata": metadata,
        "dry_run_evidence": dry_run,
        "settings": _settings(),
        "expected_live_path": database,
        "expected_checksum": _sha256(database),
    }
    arguments.update(overrides)
    return preflight_live_sync(database, **arguments)  # type: ignore[arg-type]


def test_live_preflight_is_offline_and_read_only(live_gate_files: tuple[Path, Path, Path]) -> None:
    database, metadata, dry_run = live_gate_files
    before = _sha256(database)

    result = _preflight(database, metadata, dry_run)

    assert result["network_connections"] == 0
    assert result["database_writes"] == 0
    assert result["mailbox_mutations"] == 0
    assert _sha256(database) == before


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"folder": "Jobs"}, "exact folder"),
        ({"since_date": date(2024, 6, 30)}, "2024-07-01"),
        ({"expected_checksum": "0" * 64}, "checksum"),
        (
            {"settings": YahooImapSettings("person@yahoo.com", "secret", "job", "localhost", 993)},
            "imap.mail.yahoo.com",
        ),
        (
            {
                "settings": YahooImapSettings(
                    "person@yahoo.com", "secret", "job", "imap.mail.yahoo.com", 143
                )
            },
            "port 993",
        ),
    ],
)
def test_live_preflight_rejects_invalid_scope(
    live_gate_files: tuple[Path, Path, Path], override: dict[str, object], message: str
) -> None:
    database, metadata, dry_run = live_gate_files
    with pytest.raises(ValueError, match=message):
        _preflight(database, metadata, dry_run, **override)


def test_live_preflight_rejects_wrong_path(
    live_gate_files: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    database, metadata, dry_run = live_gate_files
    with pytest.raises(ValueError, match="resolve exactly"):
        _preflight(database, metadata, dry_run, expected_live_path=tmp_path / "other.db")


def test_live_preflight_rejects_invalid_backup_and_dry_run(
    live_gate_files: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    database, metadata, dry_run = live_gate_files
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="does not exist"):
        _preflight(database, missing, dry_run)

    evidence = json.loads(dry_run.read_text())
    evidence["failure_count"] = 1
    dry_run.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="failure_count"):
        _preflight(database, metadata, dry_run)


def test_live_preflight_rejects_backup_checksum_mismatch(
    live_gate_files: tuple[Path, Path, Path]
) -> None:
    database, metadata, dry_run = live_gate_files
    evidence = json.loads(metadata.read_text())
    evidence["checksum_sha256"] = "0" * 64
    metadata.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Backup checksum"):
        _preflight(database, metadata, dry_run)


def test_live_preflight_rejects_wrong_database_revision(
    live_gate_files: tuple[Path, Path, Path]
) -> None:
    database, metadata, dry_run = live_gate_files
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE alembic_version SET version_num='20260712_0005'")
    with pytest.raises(ValueError, match="revision 20260808_0007"):
        _preflight(database, metadata, dry_run, expected_checksum=_sha256(database))


def test_live_preflight_rejects_absent_credentials(
    live_gate_files: tuple[Path, Path, Path]
) -> None:
    database, metadata, dry_run = live_gate_files
    missing = YahooImapSettings("", "", "job", "imap.mail.yahoo.com", 993)
    with pytest.raises(ValueError, match="credentials"):
        _preflight(database, metadata, dry_run, settings=missing)


@pytest.mark.parametrize(
    ("allow", "token", "start_uid", "limit", "message"),
    [
        (False, LIVE_CONFIRMATION_TOKEN, FIRST_LIVE_UID, FIRST_LIVE_LIMIT, "allow-live"),
        (True, None, FIRST_LIVE_UID, FIRST_LIVE_LIMIT, "token"),
        (True, "wrong", FIRST_LIVE_UID, FIRST_LIVE_LIMIT, "token"),
        (True, LIVE_CONFIRMATION_TOKEN, 1, FIRST_LIVE_LIMIT, "53290"),
        (True, LIVE_CONFIRMATION_TOKEN, FIRST_LIVE_UID, None, "limit 100"),
        (True, LIVE_CONFIRMATION_TOKEN, FIRST_LIVE_UID, 101, "limit 100"),
    ],
)
def test_first_live_batch_gate_rejections(
    allow: bool, token: str | None, start_uid: int, limit: int | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        authorize_first_live_batch(
            allow_live=allow, confirmation=token, start_uid=start_uid, limit=limit
        )


def test_first_live_batch_gate_accepts_exact_values() -> None:
    authorize_first_live_batch(
        allow_live=True,
        confirmation=LIVE_CONFIRMATION_TOKEN,
        start_uid=FIRST_LIVE_UID,
        limit=FIRST_LIVE_LIMIT,
    )


def test_live_cli_gate_rejects_after_uid_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import sync_yahoo_imap

    database = tmp_path / "live.db"
    database.touch()
    arguments = argparse.Namespace(
        database=database,
        backup_metadata=tmp_path / "backup.json",
        dry_run_evidence=tmp_path / "dry.json",
        after_uid=53289,
        start_uid=None,
        folder="job",
        since_date=SINCE_DATE,
        allow_live_database=True,
        confirm_live_sync=LIVE_CONFIRMATION_TOKEN,
        limit=100,
    )
    monkeypatch.setattr(sync_yahoo_imap, "EXPECTED_LIVE_DATABASE", database)
    monkeypatch.setattr(sync_yahoo_imap, "live_preflight", lambda *args: {})

    with pytest.raises(ValueError, match="explicit --start-uid"):
        sync_yahoo_imap.live_sync_database(arguments, _settings(), start_uid=53290)


def test_cli_error_redacts_credentials() -> None:
    environment = os.environ.copy()
    secret = "synthetic-sensitive-app-password"
    environment.update(
        {"YAHOO_IMAP_USERNAME": "private@yahoo.com", "YAHOO_IMAP_APP_PASSWORD": secret}
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/sync_yahoo_imap.py",
            "--preflight-live",
            "--folder",
            "job",
            "--since-date",
            "2024-07-01",
            "--database",
            "data/jobs.db",
            "--backup-metadata",
            "missing.json",
            "--dry-run-evidence",
            "missing.json",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert secret not in completed.stderr
    assert "private@yahoo.com" not in completed.stderr


def test_output_json_writes_exact_sanitized_result(tmp_path: Path) -> None:
    result = {
        "mode": "dry-run",
        "folder": "job",
        "since_date": "2024-07-01",
        "accepted_candidates": 100,
        "failures": [],
        "database_writes": 0,
        "mailbox_mutations": 0,
    }
    output = tmp_path / "evidence.json"

    resolved = write_json_evidence(output, result)

    assert resolved == output.resolve()
    assert json.loads(output.read_text()) == result


@pytest.mark.parametrize("field", ["subject", "sender", "app_password", "message_id"])
def test_output_json_rejects_sensitive_fields(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValueError, match="forbidden fields"):
        write_json_evidence(tmp_path / "evidence.json", {field: "sensitive"})


def test_output_json_rejects_repository_path() -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        write_json_evidence(ROOT / "evidence.json", {"mode": "dry-run"})
