#!/usr/bin/env python3
"""List or safely synchronize a Yahoo IMAP Jobs folder without mailbox mutations."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from backend.app.services.email_classification import classify_email  # noqa: E402
from backend.app.services.imap_checkpoint import (  # noqa: E402
    ImapCheckpoint,
    read_checkpoint,
    verify_sync_database,
    write_checkpoint,
)
from backend.app.services.yahoo_live_sync import (  # noqa: E402
    EXPECTED_LIVE_DATABASE,
    FIRST_LIVE_UID,
    authorize_first_live_batch,
    checkpoint_evidence,
    database_state,
    idempotency_evidence,
    idempotency_token,
    preflight_live_sync,
    state_delta,
)
from backend.app.services.yahoo_imap import (  # noqa: E402
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_MAX_FALLBACK_MESSAGE_BYTES,
    DEFAULT_MAX_MIME_PARTS,
    DEFAULT_PORT,
    DEFAULT_READ_TIMEOUT,
    ScanProgress,
    YahooImapScan,
    YahooImapSettings,
    list_folders,
    scan_with_reconnect,
)

PROTECTED_DATABASES = {
    (ROOT / "data" / "jobs.db").resolve(),
    (ROOT / "backend" / "jobs.db").resolve(),
    (ROOT / "backend" / "jobs.db.migrated").resolve(),
}
MINIMUM_SINCE_DATE = date(2024, 7, 1)
DEFAULT_PROGRESS_EVERY = 100


def positive_number(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def nonnegative_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return number


def parse_since_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since-date must use YYYY-MM-DD") from exc
    if parsed < MINIMUM_SINCE_DATE:
        raise argparse.ArgumentTypeError("--since-date cannot be before 2024-07-01")
    return parsed


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-folders", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--count-only", action="store_true")
    mode.add_argument("--preflight-live", action="store_true")
    mode.add_argument("--sync", action="store_true")
    parser.add_argument("--folder")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--since-date", type=parse_since_date)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-live-database", action="store_true")
    parser.add_argument("--confirm-live-sync")
    parser.add_argument("--backup-metadata", type=Path)
    parser.add_argument("--dry-run-evidence", type=Path)
    parser.add_argument("--output-json", type=Path)
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("--start-uid", type=positive_integer)
    resume.add_argument("--after-uid", type=nonnegative_integer)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--connect-timeout", type=positive_number, default=DEFAULT_CONNECT_TIMEOUT)
    parser.add_argument("--read-timeout", type=positive_number, default=DEFAULT_READ_TIMEOUT)
    parser.add_argument("--progress-every", type=positive_integer, default=DEFAULT_PROGRESS_EVERY)
    parser.add_argument("--max-mime-parts", type=positive_integer, default=DEFAULT_MAX_MIME_PARTS)
    parser.add_argument(
        "--max-fallback-message-bytes",
        type=positive_integer,
        default=DEFAULT_MAX_FALLBACK_MESSAGE_BYTES,
    )
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit <= 0:
        parser.error("--limit must be greater than zero")
    if not arguments.list_folders and not (arguments.folder or os.getenv("YAHOO_IMAP_FOLDER")):
        parser.error("--folder or YAHOO_IMAP_FOLDER is required")
    if not arguments.list_folders and arguments.since_date is None:
        parser.error("--since-date is required for count-only, dry-run, and sync")
    if (arguments.sync or arguments.preflight_live) and arguments.database is None:
        parser.error("--sync and --preflight-live require an explicit --database")
    if arguments.preflight_live and not (
        arguments.backup_metadata and arguments.dry_run_evidence
    ):
        parser.error("--preflight-live requires --backup-metadata and --dry-run-evidence")
    if arguments.output_json and not arguments.dry_run:
        parser.error("--output-json is supported only with --dry-run")
    return arguments


def refuse_protected_database(path: Path) -> Path:
    """Keep protected paths closed unless the explicit live gate has already passed."""
    resolved = path.expanduser().resolve()
    if resolved in PROTECTED_DATABASES:
        raise ValueError("Protected database requires the approval-gated live synchronization path")
    return verify_sync_database(resolved)


def dry_run_report(
    scan: YahooImapScan, *, requested_start_uid: int | None = None
) -> dict[str, Any]:
    classifications = Counter(
        classify_email(
            subject=message.subject,
            sender=message.sender,
            body=message.text_body,
        ).classification.value
        for message in scan.messages
    )
    return {
        "mode": "dry-run",
        "folder": scan.folder,
        "since_date": scan.since_date.isoformat(),
        "requested_start_uid": requested_start_uid,
        "uidvalidity": scan.uidvalidity,
        "total_matched_uid_count": scan.total_matched_uid_count,
        "partial_matched_uid_count": scan.partial_matched_uid_count,
        "batch_selected_count": scan.batch_selected_count,
        "processed_count": scan.processed_count,
        "completed_count": scan.completed_count,
        "first_uid": scan.first_uid,
        "last_uid": scan.last_uid,
        "last_uid_attempted": scan.last_uid_attempted,
        "last_uid_completed": scan.last_uid_completed,
        "search_page_count": scan.search_page_count,
        "search_complete": scan.search_complete,
        "accepted_candidates": len(scan.messages),
        "failure_count": len(scan.failures),
        "classifications": dict(sorted(classifications.items())),
        "failures": [failure.__dict__ for failure in scan.failures],
        "mailbox_mutations": 0,
        "database_writes": 0,
        "reconnect_count": scan.reconnect_count,
        **asdict(scan.metrics),
    }


def count_only_report(scan: YahooImapScan) -> dict[str, Any]:
    return {
        "mode": "count-only",
        "folder": scan.folder,
        "since_date": scan.since_date.isoformat(),
        "uidvalidity": scan.uidvalidity,
        "total_matched_uid_count": scan.total_matched_uid_count,
        "partial_matched_uid_count": scan.partial_matched_uid_count,
        "first_uid": scan.first_uid,
        "last_uid": scan.last_uid,
        "search_page_count": scan.search_page_count,
        "search_complete": scan.search_complete,
        "mailbox_mutations": 0,
        "database_writes": 0,
        **asdict(scan.metrics),
    }


def write_progress(progress: ScanProgress) -> None:
    print(json.dumps(asdict(progress), sort_keys=True), file=sys.stderr, flush=True)


def write_json_evidence(path: Path, result: dict[str, Any]) -> Path:
    """Write sanitized dry-run evidence outside the repository."""
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(ROOT):
        raise ValueError("Yahoo dry-run evidence must be written outside the repository")
    forbidden = {
        "username",
        "password",
        "app_password",
        "subject",
        "sender",
        "recipients",
        "body",
        "text_body",
        "message_id",
        "raw_mime",
    }
    present = forbidden.intersection(_nested_keys(result))
    if present:
        raise ValueError(f"Yahoo dry-run evidence contains forbidden fields: {', '.join(sorted(present))}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return resolved


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value}.union(
            *(_nested_keys(item) for item in value.values()), set()
        )
    if isinstance(value, (list, tuple)):
        return set().union(*(_nested_keys(item) for item in value), set())
    return set()


def synchronize(
    settings: YahooImapSettings,
    database: Path,
    *,
    folder: str,
    since_date: date,
    limit: int | None,
    start_uid: int = 1,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    verify_idempotency: bool = False,
) -> dict[str, Any]:
    before = database_state(database)
    checkpoint = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace=settings.account_namespace,
        folder=folder,
        since_date=since_date,
    )
    started_at = datetime.now(UTC).replace(tzinfo=None)
    scan = scan_with_reconnect(
        settings,
        folder=folder,
        since_date=since_date,
        start_uid=max(
            start_uid,
            (checkpoint.last_successful_uid + 1) if checkpoint else 1,
        ),
        limit=limit,
        expected_uidvalidity=checkpoint.uidvalidity if checkpoint else None,
        progress_every=progress_every,
        progress_callback=write_progress,
    )
    os.environ["JOBS_DB_PATH"] = str(database)
    module = importlib.import_module("backend.main")
    imported: dict[str, Any] = module.import_yahoo_imap_messages(scan.messages)
    idempotency_result = _verify_immediate_idempotency(
        module, database, scan, enabled=verify_idempotency
    )
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    failures = [failure.__dict__ for failure in scan.failures] + list(imported["failures"])
    last_uid = _last_successful_uid(scan, imported, checkpoint)
    write_checkpoint(
        database,
        ImapCheckpoint(
            provider="yahoo",
            account_namespace=settings.account_namespace,
            folder=folder,
            since_date=since_date,
            uidvalidity=scan.uidvalidity,
            last_successful_uid=last_uid,
            sync_started_at=started_at,
            sync_completed_at=completed_at,
            scanned_count=scan.processed_count,
            accepted_count=int(imported["accepted_count"]),
            skipped_count=int(imported["skipped_count"]),
            failure_count=len(failures),
        ),
    )
    stored_checkpoint = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace=settings.account_namespace,
        folder=folder,
        since_date=since_date,
    )
    after = database_state(database)
    return {
        "mode": "sync",
        "database": str(database),
        "folder": folder,
        "since_date": since_date.isoformat(),
        "uidvalidity": scan.uidvalidity,
        "last_successful_uid": last_uid,
        "total_matched_uid_count": scan.total_matched_uid_count,
        "partial_matched_uid_count": scan.partial_matched_uid_count,
        "batch_selected_count": scan.batch_selected_count,
        "processed_count": scan.processed_count,
        "completed_count": scan.completed_count,
        "first_uid": scan.first_uid,
        "last_uid": scan.last_uid,
        "last_uid_attempted": scan.last_uid_attempted,
        "last_uid_completed": scan.last_uid_completed,
        "accepted_candidates": imported["accepted_count"],
        "skipped_count": imported["skipped_count"],
        "failure_count": len(failures),
        "failures": failures,
        "mailbox_mutations": 0,
        "database_writes": int(imported["accepted_count"]),
        "reconnect_count": scan.reconnect_count,
        "search_page_count": scan.search_page_count,
        "search_complete": scan.search_complete,
        "pre_sync_database": before,
        "post_sync_database": after,
        "table_deltas": state_delta(before, after),
        "checkpoint": checkpoint_evidence(stored_checkpoint),
        "unresolved_evidence_count": after["unresolved_evidence_count"],
        "classification_counts": after["classification_counts"],
        "idempotency_token": idempotency_token(
            account=settings.account_namespace,
            folder=folder,
            since_date=since_date,
            uidvalidity=scan.uidvalidity,
            first_uid=scan.first_uid,
            last_uid=scan.last_uid_completed,
        ),
        "idempotency_verification": idempotency_evidence(database),
        "immediate_second_pass": idempotency_result,
        **asdict(scan.metrics),
    }


def _verify_immediate_idempotency(
    module: Any, database: Path, scan: YahooImapScan, *, enabled: bool
) -> dict[str, Any]:
    if not enabled:
        return {"performed": False}
    before = database_state(database)
    repeated: dict[str, Any] = module.import_yahoo_imap_messages(scan.messages)
    after = database_state(database)
    unchanged = before["checksum_sha256"] == after["checksum_sha256"]
    passed = (
        unchanged
        and int(repeated["accepted_count"]) == 0
        and int(repeated["skipped_count"]) == len(scan.messages)
    )
    if not passed:
        raise RuntimeError("Immediate Yahoo sync idempotency verification failed")
    return {
        "performed": True,
        "passed": True,
        "accepted_count": 0,
        "skipped_count": int(repeated["skipped_count"]),
        "logical_state_unchanged": True,
        "network_connections": 0,
    }


def live_preflight(arguments: argparse.Namespace, settings: YahooImapSettings) -> dict[str, Any]:
    return preflight_live_sync(
        arguments.database,
        folder=arguments.folder,
        since_date=arguments.since_date,
        backup_metadata=arguments.backup_metadata,
        dry_run_evidence=arguments.dry_run_evidence,
        settings=settings,
    )


def live_sync_database(
    arguments: argparse.Namespace, settings: YahooImapSettings, *, start_uid: int
) -> Path:
    resolved = arguments.database.expanduser().resolve()
    if resolved != EXPECTED_LIVE_DATABASE:
        return refuse_protected_database(resolved)
    if not arguments.backup_metadata or not arguments.dry_run_evidence:
        raise ValueError("Live synchronization requires backup metadata and dry-run evidence")
    live_preflight(arguments, settings)
    if arguments.after_uid is not None or arguments.start_uid != FIRST_LIVE_UID:
        raise ValueError("First live synchronization requires explicit --start-uid 53290")
    authorize_first_live_batch(
        allow_live=arguments.allow_live_database,
        confirmation=arguments.confirm_live_sync,
        start_uid=start_uid,
        limit=arguments.limit,
    )
    return verify_sync_database(resolved)


def _last_successful_uid(
    scan: YahooImapScan, imported: dict[str, Any], checkpoint: ImapCheckpoint | None
) -> int:
    failed_uids = [failure.uid for failure in scan.failures]
    failed_uids.extend(int(item["uid"]) for item in imported["failures"])
    previous = checkpoint.last_successful_uid if checkpoint else 0
    if failed_uids:
        return max(previous, min(failed_uids) - 1)
    return max(previous, scan.highest_contiguous_uid)


def main() -> None:
    arguments = parse_arguments()
    folder = arguments.folder or os.getenv("YAHOO_IMAP_FOLDER", "")
    start_uid = (
        arguments.after_uid + 1 if arguments.after_uid is not None else (arguments.start_uid or 1)
    )
    try:
        settings = YahooImapSettings.from_environment(
            folder=folder,
            host=arguments.host,
            port=arguments.port,
            connect_timeout=arguments.connect_timeout,
            read_timeout=arguments.read_timeout,
            max_mime_parts=arguments.max_mime_parts,
            max_fallback_message_bytes=arguments.max_fallback_message_bytes,
        )
        if arguments.list_folders:
            result: dict[str, Any] = {"folders": list(list_folders(settings))}
        elif arguments.preflight_live:
            result = live_preflight(arguments, settings)
        elif arguments.count_only:
            result = count_only_report(
                scan_with_reconnect(
                    settings,
                    folder=folder,
                    since_date=arguments.since_date,
                    start_uid=start_uid,
                    count_only=True,
                )
            )
        elif arguments.dry_run:
            result = dry_run_report(
                scan_with_reconnect(
                    settings,
                    folder=folder,
                    since_date=arguments.since_date,
                    start_uid=start_uid,
                    limit=arguments.limit,
                    progress_every=arguments.progress_every,
                    progress_callback=write_progress,
                ),
                requested_start_uid=start_uid,
            )
        else:
            database = live_sync_database(arguments, settings, start_uid=start_uid)
            result = synchronize(
                settings,
                database,
                folder=folder,
                since_date=arguments.since_date,
                start_uid=start_uid,
                limit=arguments.limit,
                progress_every=arguments.progress_every,
                verify_idempotency=database == EXPECTED_LIVE_DATABASE,
            )
    except Exception as exc:
        raise SystemExit(f"Yahoo IMAP operation stopped: {exc}") from exc
    if arguments.output_json:
        write_json_evidence(arguments.output_json, result)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
