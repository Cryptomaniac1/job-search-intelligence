"""Safety and evidence utilities for historical interview replay rehearsals."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .historical_interview_import import (
    HistoricalMessage,
    HistoricalMessageAnalysis,
    analyze_historical_message,
    iter_mbox_messages,
    iter_yahoo_messages,
)
from .recruiter_crm import extract_recruiter

EXPECTED_REVISION = "20260712_0005"
TABLES = (
    "jobs",
    "email_imports",
    "imported_messages",
    "email_classifications",
    "recruiters",
    "recruiter_company_links",
    "recruiter_email_addresses",
    "recruiter_job_links",
    "interviews",
    "interview_events",
)
ADDITIVE_TABLES = frozenset(TABLES) - {"jobs"}
CSV_FIELDS = (
    "provider",
    "source",
    "ordinal",
    "message_identity",
    "status",
    "classification",
    "event_type",
    "job_identifier",
    "recruiter_candidate",
    "ambiguity_reasons",
    "error",
)


@dataclass(frozen=True)
class ProviderInput:
    """One independently supplied historical provider export."""

    provider: str
    path: Path


@dataclass(frozen=True)
class TableEvidence:
    """Stable count and logical digest for one SQLite table."""

    count: int
    digest: str
    maximum_id: int | None
    existing_rows_digest: str


@dataclass(frozen=True)
class DatabaseEvidence:
    """Read-only evidence captured from a rehearsal database."""

    checksum: str
    revision: str
    tables: dict[str, TableEvidence]
    integrity_check: str
    foreign_key_violations: tuple[tuple[Any, ...], ...]


Analyzer = Callable[[HistoricalMessage], HistoricalMessageAnalysis]


def file_checksum(path: Path) -> str:
    """Return the SHA-256 digest of a file without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_database(path: Path) -> Path:
    """Require a readable Interview Pipeline database at the expected revision."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Source database does not exist: {resolved}")
    evidence = capture_database_evidence(resolved)
    if evidence.revision != EXPECTED_REVISION:
        raise ValueError(
            f"Source database must be at {EXPECTED_REVISION}; found {evidence.revision or 'none'}"
        )
    return resolved


def validate_output_directory(path: Path, repository_root: Path) -> Path:
    """Keep disposable databases and reports outside the repository."""
    resolved = path.expanduser().resolve()
    repository = repository_root.resolve()
    if resolved == repository or repository in resolved.parents:
        raise ValueError("Output directory must be outside the repository")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def create_sqlite_backup(source: Path, destination: Path) -> None:
    """Create a consistent SQLite backup using a read-only source connection."""
    if source.resolve() == destination.resolve():
        raise ValueError("Disposable database must not target the source database")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def capture_database_evidence(
    path: Path, *, baseline_maximum_ids: dict[str, int | None] | None = None
) -> DatabaseEvidence:
    """Capture checksums, revision, table digests, and SQLite safety checks."""
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            table: _table_evidence(
                connection,
                table,
                existing_maximum_id=(
                    -1
                    if baseline_maximum_ids is not None
                    and table in baseline_maximum_ids
                    and baseline_maximum_ids[table] is None
                    else (baseline_maximum_ids or {}).get(table)
                ),
            )
            for table in TABLES
        }
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = tuple(
            tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()
        )
    return DatabaseEvidence(
        checksum=file_checksum(path),
        revision=str(revision_row[0]) if revision_row else "",
        tables=tables,
        integrity_check=integrity,
        foreign_key_violations=foreign_keys,
    )


def validate_provider_inputs(inputs: Sequence[ProviderInput]) -> None:
    """Validate only the independently supplied provider inputs."""
    if not inputs:
        raise ValueError("At least one provider input is required")
    for provider_input in inputs:
        path = provider_input.path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"{provider_input.provider} input does not exist: {path}")
        if provider_input.provider in {"gmail", "hotmail"}:
            _validate_mbox(path, provider_input.provider)
        elif provider_input.provider == "yahoo":
            _validate_yahoo(path)
        else:
            raise ValueError(f"Unsupported provider: {provider_input.provider}")


def build_candidate_report(
    inputs: Sequence[ProviderInput],
    *,
    analyzer: Analyzer = analyze_historical_message,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build explainable pre-import candidate rows and a failure ledger."""
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for provider_input in inputs:
        for ordinal, message in enumerate(_messages(provider_input), start=1):
            try:
                rows.append(_candidate_row(provider_input, ordinal, message, analyzer(message)))
            except Exception as exc:  # candidate failures must be reviewable, not hidden
                failure = {
                    "provider": provider_input.provider,
                    "source": str(provider_input.path.resolve()),
                    "ordinal": str(ordinal),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures.append(failure)
                rows.append(_failure_row(provider_input, ordinal, failure["error"]))
    return rows, failures


def summarize_candidates(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Summarize candidate evidence before any replay writes occur."""
    statuses = {status: 0 for status in ("supported", "skipped", "conflicting", "failure")}
    for row in rows:
        statuses[str(row["status"])] += 1
    return {
        "messages_scanned": len(rows),
        "supported_interview_candidates": statuses["supported"],
        "skipped_messages": statuses["skipped"],
        "conflicting_classifications": statuses["conflicting"],
        "unresolved_job_links": sum(
            row["status"] == "supported" and not row["job_identifier"] for row in rows
        ),
        "recruiter_candidates": sum(bool(row["recruiter_candidate"]) for row in rows),
        "failures": statuses["failure"],
    }


def run_rehearsal(
    *,
    source_database: Path,
    output_directory: Path,
    inputs: Sequence[ProviderInput],
    repository_root: Path,
    cleanup: bool = False,
) -> dict[str, Any]:
    """Rehearse identical historical replay twice against a disposable copy."""
    source = validate_source_database(source_database)
    output = validate_output_directory(output_directory, repository_root)
    validate_provider_inputs(inputs)
    run_directory = _run_directory(output)
    run_directory.mkdir()
    disposable = run_directory / "rehearsal.db"
    evidence_path = run_directory / "rehearsal-evidence.json"
    csv_path = run_directory / "candidate-report.csv"
    source_checksum_before = file_checksum(source)
    create_sqlite_backup(source, disposable)
    source_before = capture_database_evidence(source)
    copy_before = capture_database_evidence(disposable)
    if not _logical_copy_matches(source_before, copy_before):
        raise RuntimeError("Disposable SQLite backup does not match the source database")
    rows, failures = build_candidate_report(inputs)
    _write_csv(csv_path, rows)
    evidence = _initial_evidence(
        source, disposable, inputs, source_before, copy_before, rows, failures
    )
    if failures:
        evidence["success"] = False
        evidence["failure_reason"] = "Candidate report contains failures; replay was not started"
    else:
        try:
            _execute_two_passes(evidence, disposable, inputs, repository_root, copy_before)
        except Exception as exc:
            evidence["success"] = False
            evidence["failure_reason"] = f"{type(exc).__name__}: {exc}"
            evidence["failure_ledger"].append(
                {"provider": "", "source": "", "ordinal": "", "error": str(exc)}
            )
    source_checksum_after = file_checksum(source)
    evidence["source_checksum_after"] = source_checksum_after
    evidence["source_preserved"] = source_checksum_after == source_checksum_before
    if not evidence["source_preserved"]:
        evidence["success"] = False
        evidence["failure_reason"] = "Source database checksum changed during rehearsal"
    evidence["evidence_json"] = str(evidence_path)
    evidence["candidate_csv"] = str(csv_path)
    evidence["run_directory"] = str(run_directory)
    evidence["disposable_database"] = str(disposable)
    evidence["cleaned_up"] = cleanup
    _write_json(evidence_path, evidence)
    if cleanup:
        shutil.rmtree(run_directory)
    return evidence


def _execute_two_passes(
    evidence: dict[str, Any],
    database: Path,
    inputs: Sequence[ProviderInput],
    repository_root: Path,
    before: DatabaseEvidence,
) -> None:
    maxima = {table: item.maximum_id for table, item in before.tables.items()}
    unresolved_before = _count_where(database, "interview_events", "job_id IS NULL")
    first_result = _run_import(database, inputs, repository_root)
    first = capture_database_evidence(database, baseline_maximum_ids=maxima)
    unresolved_first = _count_where(database, "interview_events", "job_id IS NULL")
    second_result = _run_import(database, inputs, repository_root)
    second = capture_database_evidence(database, baseline_maximum_ids=maxima)
    validations = _validate_rehearsal(before, first, second)
    evidence.update(
        {
            "first_run": first_result,
            "second_run": second_result,
            "after_first_run": _database_dict(first),
            "after_second_run": _database_dict(second),
            "created": _created_counts(before, first)
            | {"unresolved_records": unresolved_first - unresolved_before},
            "validations": validations,
            "success": all(validations.values()),
            "failure_ledger": evidence["failure_ledger"]
            + _import_failures(first_result, second_result),
        }
    )


def _run_import(
    database: Path, inputs: Sequence[ProviderInput], repository_root: Path
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(repository_root / "scripts" / "import_historical_interviews.py"),
        "--database",
        str(database),
    ]
    for provider_input in inputs:
        option = (
            "--yahoo-json"
            if provider_input.provider == "yahoo"
            else f"--{provider_input.provider}-mbox"
        )
        command.extend((option, str(provider_input.path)))
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        command,
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "Historical replay failed"
        )
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def _validate_rehearsal(
    before: DatabaseEvidence, first: DatabaseEvidence, second: DatabaseEvidence
) -> dict[str, bool]:
    return {
        "revision_unchanged": first.revision == before.revision == second.revision,
        "jobs_count_unchanged": first.tables["jobs"].count == before.tables["jobs"].count,
        "jobs_digest_unchanged": first.tables["jobs"].digest == before.tables["jobs"].digest,
        "pre_existing_rows_unchanged": _pre_existing_rows_unchanged(before, first),
        "second_run_counts_identical": _counts(first) == _counts(second),
        "second_run_digests_identical": _digests(first) == _digests(second),
        "integrity_check_ok": first.integrity_check == second.integrity_check == "ok",
        "foreign_key_check_ok": not first.foreign_key_violations
        and not second.foreign_key_violations,
    }


def _pre_existing_rows_unchanged(before: DatabaseEvidence, after: DatabaseEvidence) -> bool:
    return (
        all(
            before.tables[table].existing_rows_digest == after.tables[table].existing_rows_digest
            for table in ADDITIVE_TABLES
        )
        and before.tables["jobs"].digest == after.tables["jobs"].digest
    )


def _table_evidence(
    connection: sqlite3.Connection,
    table: str,
    *,
    existing_maximum_id: int | None = None,
) -> TableEvidence:
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        raise ValueError(f"Required table is missing: {table}")
    count = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    maximum_id = (
        int(connection.execute(f'SELECT MAX(id) FROM "{table}"').fetchone()[0])
        if count and "id" in columns
        else None
    )
    digest = _rows_digest(connection, table, columns)
    baseline = maximum_id if existing_maximum_id is None else existing_maximum_id
    existing = _rows_digest(connection, table, columns, maximum_id=baseline)
    return TableEvidence(count, digest, maximum_id, existing)


def _rows_digest(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    *,
    maximum_id: int | None = None,
) -> str:
    quoted = ", ".join(f'"{column}"' for column in columns)
    query = f'SELECT {quoted} FROM "{table}"'
    parameters: tuple[int, ...] = ()
    if maximum_id is not None and "id" in columns:
        query += " WHERE id <= ?"
        parameters = (maximum_id,)
    query += " ORDER BY " + ("id" if "id" in columns else quoted)
    digest = hashlib.sha256()
    for row in connection.execute(query, parameters):
        digest.update(json.dumps(tuple(row), default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _messages(provider_input: ProviderInput) -> Iterator[HistoricalMessage]:
    if provider_input.provider in {"gmail", "hotmail"}:
        yield from iter_mbox_messages(provider_input.path, provider_input.provider)
    else:
        yield from iter_yahoo_messages(provider_input.path)


def _candidate_row(
    provider_input: ProviderInput,
    ordinal: int,
    message: HistoricalMessage,
    analysis: HistoricalMessageAnalysis,
) -> dict[str, Any]:
    candidate = analysis.candidate
    recruiter_candidate = False
    if candidate:
        recruiter_candidate = (
            extract_recruiter(
                classification=candidate.classification.classification.value,
                sender=message.sender,
                subject=message.subject,
                body=message.body,
            )
            is not None
        )
    return {
        "provider": provider_input.provider,
        "source": str(provider_input.path.resolve()),
        "ordinal": ordinal,
        "message_identity": candidate.identity if candidate else "",
        "status": analysis.status,
        "classification": candidate.classification.classification.value if candidate else "",
        "event_type": candidate.evidence.event_type if candidate else "",
        "job_identifier": candidate.evidence.job_identifier if candidate else "",
        "recruiter_candidate": recruiter_candidate,
        "ambiguity_reasons": " | ".join(candidate.evidence.ambiguity_reasons) if candidate else "",
        "error": "",
    }


def _failure_row(provider_input: ProviderInput, ordinal: int, error: str) -> dict[str, Any]:
    return {
        "provider": provider_input.provider,
        "source": str(provider_input.path.resolve()),
        "ordinal": ordinal,
        "message_identity": "",
        "status": "failure",
        "classification": "",
        "event_type": "",
        "job_identifier": "",
        "recruiter_candidate": False,
        "ambiguity_reasons": "",
        "error": error,
    }


def _validate_mbox(path: Path, provider: str) -> None:
    with path.open("rb") as handle:
        separator = handle.read(5)
    if separator != b"From ":
        raise ValueError(f"Invalid {provider} MBOX: {path}: missing MBOX From separator")
    try:
        iterator = iter_mbox_messages(path, provider)
        next(iterator, None)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {provider} MBOX: {path}: {exc}") from exc


def _validate_yahoo(path: Path) -> None:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid Yahoo JSON: {path}: {exc}") from exc
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list) or not records:
        raise ValueError("Yahoo JSON must contain a non-empty raw-message list")
    if not all(
        isinstance(record, dict)
        and all(isinstance(record.get(field), str) for field in ("subject", "sender", "body"))
        for record in records
    ):
        raise ValueError("Yahoo JSON records require raw subject, sender, and body strings")


def _initial_evidence(
    source: Path,
    disposable: Path,
    inputs: Sequence[ProviderInput],
    source_before: DatabaseEvidence,
    copy_before: DatabaseEvidence,
    rows: Sequence[dict[str, Any]],
    failures: Sequence[dict[str, str]],
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_database": str(source),
        "disposable_database": str(disposable),
        "source_before": _database_dict(source_before),
        "copy_before": _database_dict(copy_before),
        "inputs": [asdict(item) | {"path": str(item.path.resolve())} for item in inputs],
        "candidate_summary": summarize_candidates(rows),
        "failure_ledger": list(failures),
    }


def _database_dict(evidence: DatabaseEvidence) -> dict[str, Any]:
    return {
        "checksum": evidence.checksum,
        "revision": evidence.revision,
        "tables": {table: asdict(value) for table, value in evidence.tables.items()},
        "integrity_check": evidence.integrity_check,
        "foreign_key_violations": list(evidence.foreign_key_violations),
    }


def _created_counts(before: DatabaseEvidence, after: DatabaseEvidence) -> dict[str, int]:
    return {
        "recruiters": after.tables["recruiters"].count - before.tables["recruiters"].count,
        "interviews": after.tables["interviews"].count - before.tables["interviews"].count,
        "interview_events": (
            after.tables["interview_events"].count - before.tables["interview_events"].count
        ),
    }


def _counts(evidence: DatabaseEvidence) -> dict[str, int]:
    return {table: item.count for table, item in evidence.tables.items()}


def _digests(evidence: DatabaseEvidence) -> dict[str, str]:
    return {table: item.digest for table, item in evidence.tables.items()}


def _logical_copy_matches(source: DatabaseEvidence, copy: DatabaseEvidence) -> bool:
    return (
        source.revision == copy.revision
        and _counts(source) == _counts(copy)
        and _digests(source) == _digests(copy)
        and copy.integrity_check == "ok"
        and not copy.foreign_key_violations
    )


def _count_where(path: Path, table: str, predicate: str) -> int:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}').fetchone()
    return int(row[0])


def _import_failures(*results: dict[str, Any]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for run_number, result in enumerate(results, start=1):
        for source in result.get("sources", []):
            if source.get("failures"):
                failures.append(
                    {
                        "run": str(run_number),
                        "provider": str(source.get("provider", "")),
                        "source": str(source.get("source", "")),
                        "error": str(source["failures"]),
                    }
                )
    return failures


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    path.write_text(serialized, encoding="utf-8")


def _run_directory(output: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return output / f"historical-interview-rehearsal-{stamp}-{uuid.uuid4().hex[:8]}"
