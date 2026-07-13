from __future__ import annotations

import json
import mailbox
import os
import sqlite3
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from backend.app.services.historical_interview_import import (
    HistoricalMessage,
    HistoricalMessageAnalysis,
)
from backend.app.services.historical_interview_rehearsal import (
    ProviderInput,
    build_candidate_report,
    file_checksum,
    run_rehearsal,
    validate_output_directory,
    validate_provider_inputs,
)

ROOT = Path(__file__).resolve().parents[1]


def create_source_database(tmp_path: Path) -> Path:
    database = tmp_path / "source.db"
    previous = os.environ.get("JOBS_DB_PATH")
    os.environ["JOBS_DB_PATH"] = str(database)
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = previous
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO jobs (
                   linkedin_job_id, title, company, location, salary_text,
                   applicant_count_is_over, applicant_text, easy_apply, promoted,
                   posted_text, work_mode, description, url, source, status, notes,
                   score, first_seen_at, last_seen_at, requisition_id
               ) VALUES (
                   'REQ-8800', 'Platform Engineer', 'Acme', '', '',
                   0, '', 0, 0, '', '', '', '', 'test', 'applied', '',
                   0, '2027-01-01 12:00:00', '2027-01-01 12:00:00', 'REQ-8800'
               )"""
        )
    return database


def write_mbox(path: Path, messages: list[tuple[str, str, str]]) -> Path:
    archive = mailbox.mbox(path, create=True)
    try:
        for index, (subject, sender, body) in enumerate(messages, start=1):
            message = EmailMessage()
            message["Message-ID"] = f"<{path.stem}-{index}@example.invalid>"
            message["Subject"] = subject
            message["From"] = sender
            message["Date"] = "Fri, 2 Jan 2027 12:00:00 -0800"
            message.set_content(body)
            archive.add(message)
        archive.flush()
    finally:
        archive.close()
    return path


def invitation(job_identifier: str = "REQ-8800") -> tuple[str, str, str]:
    return (
        "Interview invitation",
        "Avery Recruiter <avery@acme.example>",
        f"Schedule your interview for Job ID: {job_identifier}. Senior Recruiter at Acme.",
    )


def rehearsal(
    tmp_path: Path,
    inputs: list[ProviderInput],
    *,
    cleanup: bool = False,
) -> tuple[dict[str, Any], Path, str]:
    source = create_source_database(tmp_path)
    checksum = file_checksum(source)
    evidence = run_rehearsal(
        source_database=source,
        output_directory=tmp_path / "evidence",
        inputs=inputs,
        repository_root=ROOT,
        cleanup=cleanup,
    )
    return evidence, source, checksum


def test_gmail_only_rehearsal_preserves_source_and_is_idempotent(tmp_path: Path) -> None:
    gmail = write_mbox(tmp_path / "gmail.mbox", [invitation()])
    evidence, source, checksum = rehearsal(tmp_path, [ProviderInput("gmail", gmail)])

    assert evidence["success"] is True
    assert evidence["source_preserved"] is True
    assert file_checksum(source) == checksum
    assert evidence["candidate_summary"]["messages_scanned"] == 1
    assert evidence["created"] == {
        "recruiters": 1,
        "interviews": 1,
        "interview_events": 1,
        "unresolved_records": 0,
    }
    validations = evidence["validations"]
    assert validations["jobs_count_unchanged"] is True
    assert validations["jobs_digest_unchanged"] is True
    assert validations["pre_existing_rows_unchanged"] is True
    assert validations["second_run_counts_identical"] is True
    assert validations["second_run_digests_identical"] is True
    evidence_path = Path(str(evidence["evidence_json"]))
    csv_path = Path(str(evidence["candidate_csv"]))
    assert json.loads(evidence_path.read_text())["success"] is True
    assert "message_identity" in csv_path.read_text()


def test_hotmail_only_rehearsal(tmp_path: Path) -> None:
    hotmail = write_mbox(tmp_path / "hotmail.mbox", [invitation()])
    evidence, _, _ = rehearsal(tmp_path, [ProviderInput("hotmail", hotmail)])

    assert evidence["success"] is True
    assert evidence["candidate_summary"]["supported_interview_candidates"] == 1
    assert evidence["first_run"]["sources"][0]["provider"] == "hotmail"


def test_gmail_and_hotmail_rehearse_without_optional_yahoo(tmp_path: Path) -> None:
    gmail = write_mbox(tmp_path / "gmail.mbox", [invitation()])
    hotmail = write_mbox(
        tmp_path / "hotmail.mbox",
        [
            (
                "Assessment invitation",
                "Taylor Recruiter <taylor@acme.example>",
                "Complete your assessment for Job ID: REQ-8800. Senior Recruiter at Acme.",
            )
        ],
    )
    evidence, _, _ = rehearsal(
        tmp_path,
        [ProviderInput("gmail", gmail), ProviderInput("hotmail", hotmail)],
    )

    assert evidence["success"] is True
    assert evidence["candidate_summary"]["messages_scanned"] == 2
    assert len(evidence["first_run"]["sources"]) == 2


def test_invalid_yahoo_raw_message_format_is_rejected(tmp_path: Path) -> None:
    yahoo = tmp_path / "yahoo.json"
    yahoo.write_text('{"records":[{"company":"Acme","status":"Interview"}]}')

    with pytest.raises(ValueError, match="raw subject, sender, and body"):
        validate_provider_inputs([ProviderInput("yahoo", yahoo)])


def test_repository_data_directory_cannot_be_a_rehearsal_target() -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        validate_output_directory(ROOT / "data", ROOT)


def test_unresolved_evidence_is_reported(tmp_path: Path) -> None:
    gmail = write_mbox(tmp_path / "gmail.mbox", [invitation("REQ-NOT-FOUND")])
    evidence, _, _ = rehearsal(tmp_path, [ProviderInput("gmail", gmail)])

    assert evidence["success"] is True
    assert evidence["created"]["unresolved_records"] == 1
    assert evidence["first_run"]["sources"][0]["unmatched_events"] == 1


def test_candidate_failure_ledger_is_generated(tmp_path: Path) -> None:
    gmail = write_mbox(tmp_path / "gmail.mbox", [invitation()])

    def failing_analyzer(message: HistoricalMessage) -> HistoricalMessageAnalysis:
        raise RuntimeError(f"cannot analyze {message!r}")

    rows, failures = build_candidate_report(
        [ProviderInput("gmail", gmail)], analyzer=failing_analyzer
    )

    assert rows[0]["status"] == "failure"
    assert failures[0]["error"].startswith("RuntimeError: cannot analyze")


def test_cleanup_removes_only_generated_run_directory(tmp_path: Path) -> None:
    gmail = write_mbox(tmp_path / "gmail.mbox", [invitation()])
    output = tmp_path / "evidence"
    marker = output / "keep.txt"
    output.mkdir()
    marker.write_text("keep")
    source = create_source_database(tmp_path)

    evidence = run_rehearsal(
        source_database=source,
        output_directory=output,
        inputs=[ProviderInput("gmail", gmail)],
        repository_root=ROOT,
        cleanup=True,
    )

    assert evidence["success"] is True
    assert evidence["cleaned_up"] is True
    assert not Path(str(evidence["run_directory"])).exists()
    assert marker.read_text() == "keep"
