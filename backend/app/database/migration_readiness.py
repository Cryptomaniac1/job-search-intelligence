"""Read-only preflight, backup, rehearsal, and duplicate-report tooling."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIVE_DATABASE = (REPOSITORY_ROOT / "backend" / "jobs.db").resolve()
BASELINE_REVISION = "20260712_0001"
HEAD_REVISION = "20260712_0002"

EXPECTED_COLUMNS = {
    "jobs": {
        "id",
        "linkedin_job_id",
        "title",
        "company",
        "location",
        "salary_text",
        "applicant_count",
        "applicant_count_is_over",
        "applicant_text",
        "easy_apply",
        "promoted",
        "posted_text",
        "work_mode",
        "description",
        "url",
        "source",
        "status",
        "notes",
        "score",
        "first_seen_at",
        "last_seen_at",
        "email_account",
        "role_family",
        "resume_family",
        "applied_at",
        "confirmation_message_id",
        "ats_platform",
        "requisition_id",
        "application_source",
        "import_confidence",
    },
    "email_imports": {
        "id",
        "mailbox_name",
        "source_filename",
        "imported_at",
        "total_messages",
        "confirmations_found",
        "matched_jobs",
        "unmatched_jobs",
    },
}

IMPORTED_MESSAGE_COLUMNS = {
    "id",
    "provider",
    "source_import_id",
    "stable_message_identity",
    "original_message_id",
    "imported_at",
    "job_id",
    "outcome",
    "error",
}


@dataclass(frozen=True)
class DatabaseEvidence:
    path: str
    checksum_sha256: str
    size_bytes: int
    tables: list[str]
    indexes: list[dict[str, str]]
    schema: list[dict[str, str]]
    row_counts: dict[str, int]
    integrity_check: list[str]
    foreign_key_violations: list[list[Any]]
    alembic_revision: str | None
    checked_at: str


@dataclass(frozen=True)
class PreflightResult:
    compatible: bool
    state: str
    errors: list[str]
    warnings: list[str]
    evidence: DatabaseEvidence


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


def ensure_not_live_mutation(path: Path) -> None:
    if resolve_path(path) == LIVE_DATABASE:
        raise ValueError(f"Refusing to modify historical database: {LIVE_DATABASE}")


def open_read_only(path: Path) -> sqlite3.Connection:
    resolved = resolve_path(path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(connection: sqlite3.Connection, query: str) -> list[tuple[Any, ...]]:
    return list(connection.execute(query))


def collect_evidence(path: Path) -> DatabaseEvidence:
    resolved = resolve_path(path)
    with open_read_only(resolved) as connection:
        tables = [
            str(row[0])
            for row in _rows(
                connection,
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            )
            if row[0] != "sqlite_sequence"
        ]
        indexes = [
            {"name": str(row[0]), "table": str(row[1]), "sql": str(row[2] or "")}
            for row in _rows(
                connection,
                "SELECT name, tbl_name, sql FROM sqlite_master " "WHERE type='index' ORDER BY name",
            )
            if not str(row[0]).startswith("sqlite_autoindex")
        ]
        schema = [
            {"name": str(row[0]), "type": str(row[1]), "sql": str(row[2] or "")}
            for row in _rows(
                connection,
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table','index') ORDER BY type, name",
            )
        ]
        row_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
            if table != "alembic_version"
        }
        integrity = [str(row[0]) for row in _rows(connection, "PRAGMA integrity_check")]
        foreign_keys = [list(row) for row in _rows(connection, "PRAGMA foreign_key_check")]
        revision = _alembic_revision(connection, tables)
    return DatabaseEvidence(
        path=str(resolved),
        checksum_sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        tables=tables,
        indexes=indexes,
        schema=schema,
        row_counts=row_counts,
        integrity_check=integrity,
        foreign_key_violations=foreign_keys,
        alembic_revision=revision,
        checked_at=datetime.now(UTC).isoformat(),
    )


def _alembic_revision(connection: sqlite3.Connection, tables: Sequence[str]) -> str | None:
    if "alembic_version" not in tables:
        return None
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def preflight(path: Path) -> PreflightResult:
    evidence = collect_evidence(path)
    errors: list[str] = []
    warnings: list[str] = []
    with open_read_only(path) as connection:
        for table, expected in EXPECTED_COLUMNS.items():
            actual = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            if missing:
                errors.append(f"{table}: missing columns: {', '.join(missing)}")
            if unexpected:
                warnings.append(f"{table}: unexpected columns: {', '.join(unexpected)}")
        if "imported_messages" in evidence.tables:
            _check_imported_message_schema(connection, errors, warnings)
    _check_tables_and_indexes(evidence, errors, warnings)
    if evidence.integrity_check != ["ok"]:
        errors.append(f"integrity_check failed: {evidence.integrity_check}")
    if evidence.foreign_key_violations:
        errors.append(f"foreign_key_check failed: {evidence.foreign_key_violations}")
    state = _migration_state(evidence)
    return PreflightResult(not errors, state, errors, warnings, evidence)


def _check_imported_message_schema(
    connection: sqlite3.Connection,
    errors: list[str],
    warnings: list[str],
) -> None:
    actual = {str(row[1]) for row in connection.execute("PRAGMA table_info('imported_messages')")}
    missing = sorted(IMPORTED_MESSAGE_COLUMNS - actual)
    unexpected = sorted(actual - IMPORTED_MESSAGE_COLUMNS)
    if missing:
        errors.append(f"imported_messages: missing columns: {', '.join(missing)}")
    if unexpected:
        warnings.append(f"imported_messages: unexpected columns: {', '.join(unexpected)}")
    indexes = list(connection.execute("PRAGMA index_list('imported_messages')"))
    indexed_names = {str(row[1]) for row in indexes}
    unique_columns = {
        str(column[2])
        for row in indexes
        if int(row[2]) == 1
        for column in connection.execute(f'PRAGMA index_info("{row[1]}")')
    }
    if "stable_message_identity" not in unique_columns:
        errors.append("imported_messages: stable identity is not unique")
    for expected_index in {"ix_imported_messages_provider", "ix_imported_messages_job_id"}:
        if expected_index not in indexed_names:
            errors.append(f"imported_messages: missing index {expected_index}")
    foreign_tables = {
        str(row[2]) for row in connection.execute("PRAGMA foreign_key_list('imported_messages')")
    }
    if not {"jobs", "email_imports"}.issubset(foreign_tables):
        errors.append("imported_messages: expected foreign keys are missing")


def _check_tables_and_indexes(
    evidence: DatabaseEvidence,
    errors: list[str],
    warnings: list[str],
) -> None:
    tables = set(evidence.tables)
    missing_tables = {"jobs", "email_imports"} - tables
    if missing_tables:
        errors.append(f"missing tables: {', '.join(sorted(missing_tables))}")
    allowed = {"jobs", "email_imports", "imported_messages", "alembic_version"}
    unexpected_tables = sorted(tables - allowed)
    if unexpected_tables:
        warnings.append(f"unexpected tables: {', '.join(unexpected_tables)}")
    index = next(
        (item for item in evidence.indexes if item["name"] == "ix_jobs_linkedin_job_id"), None
    )
    if not index or "UNIQUE INDEX" not in index["sql"].upper():
        errors.append("missing expected unique index ix_jobs_linkedin_job_id")


def _migration_state(evidence: DatabaseEvidence) -> str:
    if evidence.alembic_revision:
        return f"versioned:{evidence.alembic_revision}"
    if "imported_messages" in evidence.tables:
        return "unversioned:unexpected-post-baseline-schema"
    return "unversioned:baseline-compatible"


def create_backup(source: Path, output_directory: Path) -> tuple[Path, Path]:
    source = resolve_path(source)
    output_directory = resolve_path(output_directory)
    if output_directory == source.parent:
        raise ValueError("Backup directory must be outside the live database directory")
    if output_directory.is_relative_to(REPOSITORY_ROOT):
        raise ValueError("Backup directory must be outside the repository")
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_directory / f"jobs-{timestamp}.sqlite3"
    ensure_not_live_mutation(destination)
    if destination.exists():
        raise FileExistsError(destination)
    with open_read_only(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    evidence = collect_evidence(destination)
    if evidence.integrity_check != ["ok"] or evidence.foreign_key_violations:
        raise RuntimeError("Backup verification failed")
    metadata = destination.with_suffix(".metadata.json")
    metadata.write_text(json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n")
    return destination, metadata


def table_digest(path: Path, table: str) -> str:
    if table not in {"jobs", "email_imports"}:
        raise ValueError(f"Unsupported preservation table: {table}")
    digest = hashlib.sha256()
    with open_read_only(path) as connection:
        for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id'):
            digest.update(json.dumps(list(row), default=str, separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def run_alembic(database: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    database = resolve_path(database)
    ensure_not_live_mutation(database)
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def rehearse(source: Path, output_directory: Path) -> dict[str, Any]:
    preflight_result = preflight(source)
    if not preflight_result.compatible:
        raise RuntimeError(f"Preflight failed: {preflight_result.errors}")
    copy_path, metadata_path = create_backup(source, output_directory)
    before = collect_evidence(copy_path)
    before_digests = {table: table_digest(copy_path, table) for table in EXPECTED_COLUMNS}
    run_alembic(copy_path, "stamp", BASELINE_REVISION)
    run_alembic(copy_path, "upgrade", HEAD_REVISION)
    run_alembic(copy_path, "upgrade", HEAD_REVISION)
    upgraded = collect_evidence(copy_path)
    _validate_upgrade(copy_path, before.row_counts, before_digests, upgraded)
    run_alembic(copy_path, "downgrade", BASELINE_REVISION)
    rolled_back = collect_evidence(copy_path)
    _validate_rollback(copy_path, before.row_counts, before_digests, rolled_back)
    return {
        "copy": str(copy_path),
        "metadata": str(metadata_path),
        "before": asdict(before),
        "upgraded": asdict(upgraded),
        "rolled_back": asdict(rolled_back),
    }


def _validate_upgrade(
    path: Path,
    row_counts: dict[str, int],
    digests: dict[str, str],
    evidence: DatabaseEvidence,
) -> None:
    if evidence.alembic_revision != HEAD_REVISION or "imported_messages" not in evidence.tables:
        raise RuntimeError("Upgrade did not reach the expected revision and schema")
    for table in EXPECTED_COLUMNS:
        if (
            evidence.row_counts[table] != row_counts[table]
            or table_digest(path, table) != digests[table]
        ):
            raise RuntimeError(f"Upgrade changed historical table: {table}")
    with open_read_only(path) as connection:
        unique_indexes = [
            str(row[1])
            for row in connection.execute("PRAGMA index_list('imported_messages')")
            if int(row[2]) == 1
        ]
        unique_columns = {
            str(column[2])
            for index in unique_indexes
            for column in connection.execute(f'PRAGMA index_info("{index}")')
        }
        foreign_keys = {
            str(row[2])
            for row in connection.execute("PRAGMA foreign_key_list('imported_messages')")
        }
    if "stable_message_identity" not in unique_columns:
        raise RuntimeError("Imported-message unique constraint is missing")
    if not {"jobs", "email_imports"}.issubset(foreign_keys):
        raise RuntimeError("Imported-message foreign keys are missing")


def _validate_rollback(
    path: Path,
    row_counts: dict[str, int],
    digests: dict[str, str],
    evidence: DatabaseEvidence,
) -> None:
    if evidence.alembic_revision != BASELINE_REVISION or "imported_messages" in evidence.tables:
        raise RuntimeError("Rollback did not restore the baseline schema")
    for table in EXPECTED_COLUMNS:
        if (
            evidence.row_counts[table] != row_counts[table]
            or table_digest(path, table) != digests[table]
        ):
            raise RuntimeError(f"Rollback changed historical table: {table}")


DUPLICATE_FIELDS = (
    "id",
    "email_account",
    "source",
    "company",
    "title",
    "applied_at",
    "status",
    "ats_platform",
    "requisition_id",
    "import_confidence",
)


def write_duplicate_report(database: Path, output: Path) -> int:
    output = resolve_path(output)
    ensure_not_live_mutation(output)
    if output.is_relative_to(REPOSITORY_ROOT):
        raise ValueError("Duplicate report must be written outside the repository")
    output.parent.mkdir(parents=True, exist_ok=True)
    groups = _duplicate_groups(database)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_duplicate_report_fields())
        writer.writeheader()
        for message_id, records in groups:
            differences = _differing_fields(records)
            category = _duplicate_category(differences)
            writer.writerow(_duplicate_report_row(message_id, records, differences, category))
    return len(groups)


def _duplicate_groups(database: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    selected = ", ".join(DUPLICATE_FIELDS)
    query = f"""SELECT confirmation_message_id, {selected} FROM jobs
        WHERE confirmation_message_id IN (
            SELECT confirmation_message_id FROM jobs
            WHERE confirmation_message_id <> ''
            GROUP BY confirmation_message_id HAVING COUNT(*) > 1
        ) ORDER BY confirmation_message_id, id"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    with open_read_only(database) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(query):
            record = dict(row)
            message_id = str(record.pop("confirmation_message_id"))
            grouped.setdefault(message_id, []).append(record)
    return list(grouped.items())


def _differing_fields(records: Sequence[dict[str, Any]]) -> list[str]:
    return [
        field
        for field in DUPLICATE_FIELDS
        if field != "id" and len({str(record[field] or "") for record in records}) > 1
    ]


def _duplicate_category(differences: Sequence[str]) -> str:
    material = {"company", "title", "applied_at", "status", "ats_platform", "requisition_id"}
    if not differences or set(differences) <= {"source", "import_confidence"}:
        return "likely exact duplicate"
    if not material.intersection(differences):
        return "probable duplicate needing review"
    return "conflicting record requiring manual review"


def _duplicate_report_fields() -> list[str]:
    return [
        "message_id",
        *[f"{field}s" for field in DUPLICATE_FIELDS],
        "fields_that_differ",
        "recommended_category",
    ]


def _duplicate_report_row(
    message_id: str,
    records: Sequence[dict[str, Any]],
    differences: Sequence[str],
    category: str,
) -> dict[str, str]:
    row = {"message_id": message_id}
    row.update(
        {
            f"{field}s": " | ".join(str(record[field] or "") for record in records)
            for field in DUPLICATE_FIELDS
        }
    )
    row["fields_that_differ"] = ", ".join(differences)
    row["recommended_category"] = category
    return row


def _json_dump(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "backup", "rehearse", "duplicate-report"):
        command = subparsers.add_parser(name)
        command.add_argument("database", type=Path)
        if name in {"backup", "rehearse"}:
            command.add_argument("output_directory", type=Path)
        if name == "duplicate-report":
            command.add_argument("output", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.command == "preflight":
        result = preflight(args.database)
        _json_dump(asdict(result))
        return 0 if result.compatible else 1
    if args.command == "backup":
        backup, metadata = create_backup(args.database, args.output_directory)
        _json_dump({"backup": str(backup), "metadata": str(metadata)})
        return 0
    if args.command == "rehearse":
        _json_dump(rehearse(args.database, args.output_directory))
        return 0
    groups = write_duplicate_report(args.database, args.output)
    _json_dump({"groups": groups, "report": str(resolve_path(args.output))})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
