#!/usr/bin/env python3
"""Approval-gated recovery for the five unresolved Sprint 10 Yahoo UIDs."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from backend.app.services.imap_checkpoint import ImapCheckpoint, write_checkpoint  # noqa: E402
from backend.app.services.yahoo_imap import (  # noqa: E402
    YahooImapSettings,
    scan_with_reconnect,
)
from backend.app.services.yahoo_incident import (  # noqa: E402
    INCIDENT_UIDS,
    apply_missing_recovery,
    analyze_incident,
    validate_recovery_gate,
)

SINCE_DATE = date(2024, 7, 1)
FOLDER = "job"
UIDVALIDITY = "1578947209"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--recover-missing", action="store_true")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--incident-backup-metadata", required=True, type=Path)
    parser.add_argument("--dry-run-evidence", required=True, type=Path)
    parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> None:
    options = parse_arguments()
    database = options.database.expanduser().resolve()
    gate = validate_recovery_gate(
        database,
        options.incident_backup_metadata,
        options.dry_run_evidence,
        confirmation=options.confirm,
    )
    if options.preflight:
        print(json.dumps(gate, indent=2, sort_keys=True))
        return
    settings = YahooImapSettings.from_environment(folder=FOLDER)
    started_at = datetime.now(UTC).replace(tzinfo=None)
    scan = scan_with_reconnect(
        settings,
        folder=FOLDER,
        since_date=SINCE_DATE,
        start_uid=min(INCIDENT_UIDS),
        expected_uidvalidity=UIDVALIDITY,
        only_uids=set(INCIDENT_UIDS),
    )
    if scan.failures or tuple(sorted(message.uid for message in scan.messages)) != INCIDENT_UIDS:
        raise RuntimeError("Yahoo incident recovery did not fetch exactly the approved five UIDs")
    os.environ["JOBS_DB_PATH"] = str(database)
    module = importlib.import_module("backend.main")
    result = apply_missing_recovery(database, scan.messages, module.import_yahoo_imap_messages)
    analysis = analyze_incident(database, options.dry_run_evidence)
    if not analysis["checkpoint_advancement_safe"]:
        raise RuntimeError("Incident recovery evidence is incomplete; checkpoint remains absent")
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    write_checkpoint(
        database,
        ImapCheckpoint(
            provider="yahoo",
            account_namespace=settings.account_namespace,
            folder=FOLDER,
            since_date=SINCE_DATE,
            uidvalidity=UIDVALIDITY,
            last_successful_uid=53392,
            sync_started_at=started_at,
            sync_completed_at=completed_at,
            scanned_count=100,
            accepted_count=100,
            skipped_count=0,
            failure_count=0,
        ),
    )
    print(
        json.dumps(
            {
                "mode": "incident-recovery",
                "gate": gate,
                "recovery": result,
                "checkpoint_last_successful_uid": 53392,
                "mailbox_mutations": 0,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
