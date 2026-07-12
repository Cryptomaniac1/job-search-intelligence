from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


def test_representative_imports_on_migrated_disposable_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "historical-copy.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(database))
    command.upgrade(Config("alembic.ini"), "20260712_0001")
    _seed_preservation_job(database)
    command.upgrade(Config("alembic.ini"), "20260712_0002")
    sys.modules.pop("backend.main", None)
    module = importlib.import_module("backend.main")

    with TestClient(module.app) as client:
        gmail = _repeat_mbox(client, "gmail")
        hotmail = _repeat_mbox(client, "hotmail")
        yahoo = _repeat_yahoo(client)

    module.engine.dispose()
    sys.modules.pop("backend.main", None)
    assert gmail == (1, 1)
    assert hotmail == (1, 1)
    assert yahoo == (1, 1)
    _assert_rehearsal_database(database)


def _repeat_mbox(client: TestClient, provider: str) -> tuple[int, int]:
    content = (FIXTURES / f"{provider}-confirmation.mbox").read_bytes()
    responses = [
        client.post(
            "/imports/mbox",
            data={"mailbox_name": provider},
            files={"file": (f"{provider}.mbox", content, "application/mbox")},
        )
        for _ in range(2)
    ]
    assert all(response.status_code == 200 for response in responses)
    return responses[0].json()["newly_imported"], responses[1].json()["already_imported"]


def _repeat_yahoo(client: TestClient) -> tuple[int, int]:
    payload = json.loads((FIXTURES / "yahoo-confirmation.json").read_text())
    first = client.post("/imports/yahoo", json=payload)
    second = client.post("/imports/yahoo", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    return first.json()["newly_imported"], second.json()["already_imported"]


def _seed_preservation_job(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO jobs (
                linkedin_job_id, title, company, location, salary_text,
                applicant_count_is_over, applicant_text, easy_apply, promoted,
                posted_text, work_mode, description, url, source, status, notes,
                score, first_seen_at, last_seen_at
            ) VALUES ('preserved', 'Principal Product Manager', 'Preserved Company', '', '',
                      0, '', 0, 0, '', '', '', '', 'linkedin', 'saved', 'keep', 50,
                      '2026-01-01', '2026-01-01')"""
        )
        connection.commit()


def _assert_rehearsal_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        message_count = connection.execute("SELECT COUNT(*) FROM imported_messages").fetchone()[0]
        identities = connection.execute(
            "SELECT COUNT(DISTINCT stable_message_identity) FROM imported_messages"
        ).fetchone()[0]
        accounts = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT email_account FROM jobs WHERE email_account <> ''"
            )
        }
        preserved = connection.execute(
            "SELECT status, source, notes FROM jobs WHERE linkedin_job_id='preserved'"
        ).fetchone()
    assert message_count == identities == 3
    assert accounts == {"gmail", "hotmail", "yahoo"}
    assert preserved == ("saved", "linkedin", "keep")
