#!/usr/bin/env python3
"""Bounded Gmail or Hotmail OAuth IMAP synchronization."""

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
from backend.app.services.oauth_imap import OAuthImapSettings  # noqa: E402
from backend.app.services.provider_live_sync import (  # noqa: E402
    LIVE_DATABASE,
    preflight_provider_live_sync,
)
from backend.app.services.yahoo_imap import (  # noqa: E402
    ScanProgress,
    YahooImapScan,
    list_folders,
    scan_with_reconnect,
)
from backend.app.services.yahoo_incident import verify_imap_batch_read_only  # noqa: E402


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("gmail", "hotmail"))
    parser.add_argument("--folder", required=True)
    parser.add_argument("--since-date", required=True, type=_date)
    parser.add_argument("--start-uid", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--connect-timeout", type=float, default=30)
    parser.add_argument("--read-timeout", type=float, default=60)
    parser.add_argument("--max-mime-parts", type=int, default=50)
    parser.add_argument("--max-fallback-message-bytes", type=int, default=10_485_760)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output-json", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-folders", action="store_true")
    mode.add_argument("--count-only", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--preflight-live", action="store_true")
    parser.add_argument("--allow-live-database", action="store_true")
    parser.add_argument("--expected-live-sha256")
    parser.add_argument("--backup-metadata", type=Path)
    parser.add_argument("--dry-run-evidence", type=Path)
    parser.add_argument("--confirm-live-sync")
    values = parser.parse_args()
    if values.start_uid is not None and values.start_uid <= 0:
        parser.error("--start-uid must be greater than zero")
    if values.limit is not None and values.limit <= 0:
        parser.error("--limit must be greater than zero")
    if (values.sync or values.preflight_live) and values.database is None:
        parser.error("--sync and --preflight-live require --database")
    if values.preflight_live and not _has_live_gate(values):
        parser.error("--preflight-live requires checksum, backup, evidence, and confirmation")
    return values


def _has_live_gate(values: argparse.Namespace) -> bool:
    return bool(
        values.expected_live_sha256
        and values.backup_metadata
        and values.dry_run_evidence
        and values.confirm_live_sync
    )


def _settings(values: argparse.Namespace) -> OAuthImapSettings:
    return OAuthImapSettings.from_environment(
        values.provider,
        folder=values.folder,
        connect_timeout=values.connect_timeout,
        read_timeout=values.read_timeout,
        max_mime_parts=values.max_mime_parts,
        max_fallback_message_bytes=values.max_fallback_message_bytes,
    )


def _live_preflight(values: argparse.Namespace, settings: OAuthImapSettings) -> dict[str, Any]:
    if not _has_live_gate(values):
        raise ValueError("Live synchronization requires checksum, backup, evidence, and token")
    return preflight_provider_live_sync(
        values.database,
        settings=settings,
        provider=values.provider,
        folder=values.folder,
        since_date=values.since_date,
        expected_checksum=values.expected_live_sha256,
        backup_metadata=values.backup_metadata,
        dry_run_evidence=values.dry_run_evidence,
        confirmation=values.confirm_live_sync,
    )


def _progress(value: ScanProgress) -> None:
    print(
        json.dumps(asdict(value), separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
        flush=True,
    )


def _scan_report(scan: YahooImapScan, provider: str) -> dict[str, Any]:
    classifications = Counter(
        classify_email(
            subject=message.subject,
            sender=message.sender,
            body=message.text_body,
        ).classification.value
        for message in scan.messages
    )
    return {
        "provider": provider,
        "folder": scan.folder,
        "since_date": scan.since_date.isoformat(),
        "uidvalidity": scan.uidvalidity,
        "total_matched_uid_count": scan.total_matched_uid_count,
        "partial_matched_uid_count": scan.partial_matched_uid_count,
        "batch_selected_count": scan.batch_selected_count,
        "processed_count": scan.processed_count,
        "completed_count": scan.completed_count,
        "accepted_candidates": len(scan.messages),
        "failure_count": len(scan.failures),
        "first_uid": scan.first_uid,
        "last_uid": scan.last_uid,
        "last_uid_attempted": scan.last_uid_attempted,
        "last_uid_completed": scan.last_uid_completed,
        "highest_contiguous_uid": scan.highest_contiguous_uid,
        "search_page_count": scan.search_page_count,
        "search_complete": scan.search_complete,
        "reconnect_count": scan.reconnect_count,
        "classifications": dict(sorted(classifications.items())),
        "metrics": asdict(scan.metrics),
        "database_writes": 0,
        "mailbox_mutations": 0,
    }


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    resolved = path.expanduser().resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        raise ValueError("Synchronization evidence must be written outside the repository")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _checkpoint_start(
    values: argparse.Namespace, settings: OAuthImapSettings
) -> tuple[int, str | None]:
    if values.database is None:
        return values.start_uid or 1, None
    checkpoint = read_checkpoint(
        values.database,
        provider=values.provider,
        account_namespace=settings.account_namespace,
        folder=values.folder,
        since_date=values.since_date,
    )
    if checkpoint:
        return values.start_uid or checkpoint.last_successful_uid + 1, checkpoint.uidvalidity
    return values.start_uid or 1, None


def _synchronize(
    values: argparse.Namespace, settings: OAuthImapSettings, scan: YahooImapScan
) -> dict[str, Any]:
    database = verify_sync_database(values.database)
    if database == LIVE_DATABASE and not values.allow_live_database:
        raise ValueError("Live synchronization requires --allow-live-database")
    os.environ["JOBS_DB_PATH"] = str(database)
    module = importlib.import_module("backend.main")
    started_at = datetime.now(UTC).replace(tzinfo=None)
    imported: dict[str, Any] = module.import_imap_messages(scan.messages)
    verification = verify_imap_batch_read_only(database, scan.messages)
    if scan.failures or imported["failure_count"] or not verification["passed"]:
        raise RuntimeError("Provider synchronization verification failed; checkpoint not written")
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    write_checkpoint(
        database,
        ImapCheckpoint(
            provider=values.provider,
            account_namespace=settings.account_namespace,
            folder=values.folder,
            since_date=values.since_date,
            uidvalidity=scan.uidvalidity,
            last_successful_uid=scan.highest_contiguous_uid,
            sync_started_at=started_at,
            sync_completed_at=completed_at,
            scanned_count=scan.processed_count,
            accepted_count=int(imported["accepted_count"]),
            skipped_count=int(imported["skipped_count"]),
            failure_count=0,
        ),
    )
    report = _scan_report(scan, values.provider)
    report.update(
        {
            "mode": "sync",
            "import_result": imported,
            "read_only_verification": verification,
            "checkpoint_written": True,
            "database_writes": int(imported["accepted_count"]) + 1,
        }
    )
    return report


def main() -> None:
    values = arguments()
    settings = _settings(values)
    if values.preflight_live:
        report = _live_preflight(values, settings)
        _write_report(values.output_json, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    if values.list_folders:
        report = {
            "provider": values.provider,
            "folders": list(list_folders(settings)),
            "database_writes": 0,
            "mailbox_mutations": 0,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    if values.sync and values.database and values.database.expanduser().resolve() == LIVE_DATABASE:
        _live_preflight(values, settings)
    start_uid, expected_uidvalidity = _checkpoint_start(values, settings)
    scan = scan_with_reconnect(
        settings,
        folder=values.folder,
        since_date=values.since_date,
        start_uid=start_uid,
        limit=values.limit,
        expected_uidvalidity=expected_uidvalidity,
        count_only=values.count_only,
        progress_every=values.progress_every,
        progress_callback=_progress,
    )
    report = (
        _synchronize(values, settings, scan)
        if values.sync
        else _scan_report(scan, values.provider)
        | {"mode": "count-only" if values.count_only else "dry-run"}
    )
    _write_report(values.output_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
