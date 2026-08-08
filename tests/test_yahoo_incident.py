from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from backend.app.services.yahoo_imap import YahooImapMessage, imap_message_identity
from backend.app.services.yahoo_incident import (
    INCIDENT_UIDS,
    RECOVERY_CONFIRMATION_TOKEN,
    analyze_incident,
    apply_missing_recovery,
    database_digests,
    recovery_scope,
    rollback_incident_copy,
    validate_recovery_gate,
    verify_yahoo_batch_read_only,
)

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_incident" / "cases.json"
SINCE_DATE = date(2024, 7, 1)
UIDVALIDITY = "1578947209"
ACCOUNT = "fixture@yahoo.example"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _message(uid: int, subject: str, body: str, sender: str) -> YahooImapMessage:
    return YahooImapMessage(
        uid=uid,
        uidvalidity=UIDVALIDITY,
        folder="job",
        account_namespace=ACCOUNT,
        message_id=f"fixture-{uid}",
        subject=subject,
        sender=sender,
        recipients=(ACCOUNT,),
        received_at=datetime(2025, 1, 1, 12, 0),
        imap_internal_date=datetime(2025, 1, 1, 12, 0),
        requested_since_date=SINCE_DATE,
        text_body=body,
        html_fallback_used=False,
        attachments=(),
        identity=imap_message_identity(
            account_namespace=ACCOUNT,
            folder="job",
            uidvalidity=UIDVALIDITY,
            uid=uid,
        ),
    )


def _incident_messages() -> list[YahooImapMessage]:
    fixture = json.loads(FIXTURE.read_text())
    messages: dict[int, YahooImapMessage] = {}
    conflicts = {
        53314: ("CONFLICT-A", "persist-a.example"),
        53319: ("CONFLICT-LATE", "lateco.example"),
        53336: ("CONFLICT-B", "persist-b.example"),
        53355: ("CONFLICT-C", "persist-c.example"),
        53375: ("CONFLICT-D", "persist-d.example"),
        53386: ("CONFLICT-E", "persist-e.example"),
    }
    for uid, (identifier, domain) in conflicts.items():
        messages[uid] = _message(
            uid,
            "Application received",
            f"Thank you for applying. Job ID: {identifier}",
            f"updates@{domain}",
        )
    messages[fixture["dependency_creator_uid"]] = _message(
        fixture["dependency_creator_uid"],
        "Application received",
        "Thank you for applying.",
        "updates@lateco.example",
    )
    remaining = [uid for uid in range(53293, 53393) if uid not in messages]
    for index, uid in enumerate(remaining[:34]):
        company = index % 18
        messages[uid] = _message(
            uid,
            "Application received",
            "Thank you for applying.",
            f"updates@company-{company}.example",
        )
    cursor = 34
    for uid in remaining[cursor : cursor + 10]:
        messages[uid] = _message(
            uid,
            "Application update",
            "We decided not to proceed with your application.",
            "noreply@reject.example",
        )
    cursor += 10
    messages[remaining[cursor]] = _message(
        remaining[cursor],
        "Talent news",
        "Company update from our talent community.",
        "news@company.example",
    )
    cursor += 1
    for uid in remaining[cursor:]:
        messages[uid] = _message(
            uid, "Status", "No deterministic signal.", "notice@unknown.example"
        )
    result = [messages[uid] for uid in sorted(messages)]
    assert len(result) == fixture["batch_size"]
    return result


def _seed_cross_account_jobs(module: Any) -> None:
    with module.Session(module.engine) as session:
        for identifier in (
            "CONFLICT-A",
            "CONFLICT-LATE",
            "CONFLICT-B",
            "CONFLICT-C",
            "CONFLICT-D",
            "CONFLICT-E",
        ):
            session.add(
                module.Job(
                    linkedin_job_id=identifier,
                    title="Fixture role",
                    company="Fixture company",
                    source="fixture",
                    email_account="gmail",
                    first_seen_at=datetime(2024, 1, 1),
                    last_seen_at=datetime(2024, 1, 1),
                )
            )
        session.commit()


def _counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "jobs",
                "email_imports",
                "imported_messages",
                "email_classifications",
                "imap_message_metadata",
                "imap_sync_checkpoints",
            )
        }


def test_sanitized_fixture_reproduces_94_1_5_incident(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", lambda *args: set())

    first = module.import_yahoo_imap_messages(messages)
    second = module.import_yahoo_imap_messages(messages)

    assert first["accepted_count"] == 94
    assert first["failure_count"] == 6
    assert second["accepted_count"] == 1
    assert second["skipped_count"] == 94
    assert second["failure_count"] == 5
    counts = _counts(database)
    assert counts["jobs"] == 25
    assert counts["email_imports"] == 2
    assert counts["imported_messages"] == 95
    assert counts["imap_message_metadata"] == 95
    assert counts["imap_sync_checkpoints"] == 0


def test_corrected_import_is_order_independent_and_verification_is_read_only(
    isolated_app: tuple[Any, Path]
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    random.Random(42).shuffle(messages)

    result = module.import_yahoo_imap_messages(messages)
    before_checksum = _sha256(database)
    before_digests = database_digests(database)
    before_counts = _counts(database)
    verification = verify_yahoo_batch_read_only(database, messages)

    assert result["accepted_count"] == 100
    assert result["failure_count"] == 0
    assert len(result["unresolved_messages"]) == 6
    assert before_counts["jobs"] == 25
    assert before_counts["email_imports"] == 1
    with sqlite3.connect(database) as connection:
        conflict_rows = connection.execute(
            "SELECT COUNT(*) FROM imported_messages "
            "WHERE outcome='unmatched' AND error LIKE 'conflicting_identity:%'"
        ).fetchone()[0]
    assert conflict_rows == 6
    assert verification["passed"] is True
    assert verification["database_writes"] == 0
    assert verification["email_import_row_created"] is False
    assert verification["represented_count"] == 100
    assert _sha256(database) == before_checksum
    assert database_digests(database) == before_digests


def test_partial_incident_analysis_reports_five_missing_and_late_acceptance(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", lambda *args: set())
    module.import_yahoo_imap_messages(messages)
    module.import_yahoo_imap_messages(messages)
    fixture = json.loads(FIXTURE.read_text())
    dry_run = tmp_path / "dry-run.json"
    dry_run.write_text(
        json.dumps(
            {
                "first_uid": fixture["first_uid"],
                "last_uid_attempted": fixture["last_uid"],
                "batch_selected_count": 100,
                "since_date": "2024-07-01",
                "classifications": fixture["classifications"],
            }
        )
    )
    before = _sha256(database)

    report = analyze_incident(database, dry_run)

    assert report["missing_uids"] == fixture["unresolved_uids"]
    assert report["second_pass_only"][0]["uid"] == fixture["late_accepted_uid"]
    assert report["rerun_would_insert_count"] == 5
    assert report["checkpoint_advancement_safe"] is False
    assert report["database_writes"] == 0
    assert _sha256(database) == before


def test_failed_read_only_verification_leaves_checkpoint_absent(
    isolated_app: tuple[Any, Path]
) -> None:
    _, database = isolated_app
    messages = _incident_messages()
    before = _sha256(database)

    verification = verify_yahoo_batch_read_only(database, messages)

    assert verification["passed"] is False
    assert verification["missing_uids"] == list(range(53293, 53393))
    assert _counts(database)["imap_sync_checkpoints"] == 0
    assert _sha256(database) == before


def test_recovery_scope_and_disposable_rollback_preserve_baseline(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    _seed_cross_account_jobs(module)
    original = module._yahoo_cross_account_conflicts
    module._yahoo_cross_account_conflicts = lambda *args: set()
    try:
        module.import_yahoo_imap_messages(_incident_messages())
        module.import_yahoo_imap_messages(_incident_messages())
    finally:
        module._yahoo_cross_account_conflicts = original
    copy = tmp_path / "incident-copy.db"
    shutil.copy2(database, copy)
    from backend.app.services import yahoo_incident

    monkeypatch.setattr(yahoo_incident, "HISTORICAL_MAX_JOB_ID", 6)
    monkeypatch.setattr(yahoo_incident, "HISTORICAL_MAX_IMPORT_ID", 0)
    scope = recovery_scope(copy)

    assert scope["historical_rows_in_scope"] == 0
    result = rollback_incident_copy(copy)
    assert result["historical_jobs"] == 6
    assert result["historical_email_imports"] == 0
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_violations"] == []
    assert _counts(copy)["imported_messages"] == 0


def test_transport_identity_and_classifier_version_are_stable() -> None:
    first = _incident_messages()
    second = _incident_messages()

    assert [message.identity for message in first] == [message.identity for message in second]
    assert len({message.identity for message in first}) == 100


def test_controlled_missing_recovery_accepts_five_without_new_jobs(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    original_planner = module._yahoo_cross_account_conflicts
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", lambda *args: set())
    module.import_yahoo_imap_messages(messages)
    module.import_yahoo_imap_messages(messages)
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", original_planner)
    missing = [
        message for message in messages if message.uid in {53314, 53336, 53355, 53375, 53386}
    ]
    jobs_before = _counts(database)["jobs"]

    result = apply_missing_recovery(database, missing, module.import_yahoo_imap_messages)

    assert result["passed"] is True
    assert result["import_result"]["accepted_count"] == 5
    assert len(result["import_result"]["unresolved_messages"]) == 5
    assert result["after_counts"]["jobs"] == jobs_before
    assert verify_yahoo_batch_read_only(database, messages)["passed"] is True


def test_controlled_recovery_records_explicit_server_exclusions(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    original_planner = module._yahoo_cross_account_conflicts
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", lambda *args: set())
    module.import_yahoo_imap_messages(messages)
    module.import_yahoo_imap_messages(messages)
    monkeypatch.setattr(module, "_yahoo_cross_account_conflicts", original_planner)
    unavailable = (53314, 53336, 53355)
    available = [
        message for message in messages if message.uid in set(INCIDENT_UIDS) - set(unavailable)
    ]

    result = apply_missing_recovery(
        database,
        available,
        module.import_yahoo_imap_messages,
        accepted_unavailable_uids=unavailable,
    )

    assert result["passed"] is True
    assert result["uids"] == [53375, 53386]
    assert result["accepted_unavailable_uids"] == list(unavailable)
    assert result["import_result"]["accepted_count"] == 2


def test_recovery_gate_is_offline_and_checksum_bound(
    isolated_app: tuple[Any, Path], tmp_path: Path
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]
    _seed_cross_account_jobs(module)
    messages = _incident_messages()
    original = module._yahoo_cross_account_conflicts
    module._yahoo_cross_account_conflicts = lambda *args: set()
    try:
        module.import_yahoo_imap_messages(messages)
        module.import_yahoo_imap_messages(messages)
    finally:
        module._yahoo_cross_account_conflicts = original
    backup = tmp_path / "incident.sqlite3"
    shutil.copy2(database, backup)
    metadata = tmp_path / "incident.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "path": str(backup),
                "checksum_sha256": _sha256(backup),
                "alembic_revision": "20260712_0006",
            }
        )
    )
    fixture = json.loads(FIXTURE.read_text())
    dry_run = tmp_path / "dry.json"
    dry_run.write_text(
        json.dumps(
            {
                "first_uid": fixture["first_uid"],
                "last_uid_attempted": fixture["last_uid"],
                "batch_selected_count": 100,
                "since_date": "2024-07-01",
                "classifications": fixture["classifications"],
            }
        )
    )
    before = _sha256(database)

    gate = validate_recovery_gate(
        database,
        metadata,
        dry_run,
        confirmation=RECOVERY_CONFIRMATION_TOKEN,
        expected_database=database,
        expected_checksum=before,
    )

    assert gate["missing_uids"] == fixture["unresolved_uids"]
    assert gate["network_connections"] == 0
    assert gate["database_writes"] == 0
    assert _sha256(database) == before
