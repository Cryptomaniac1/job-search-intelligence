#!/usr/bin/env python3
"""Analyze or rehearse recovery of the Sprint 10 Yahoo incident without Yahoo access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from backend.app.services.yahoo_incident import (  # noqa: E402
    LIVE_DATABASE,
    analyze_incident,
    disposable_copy,
    recovery_scope,
    rollback_incident_copy,
    sha256_file,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--dry-run-evidence", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--recovery-plan", action="store_true")
    parser.add_argument("--disposable-copy", type=Path)
    parser.add_argument("--rollback-disposable", action="store_true")
    value = parser.parse_args()
    if not value.recovery_plan and not value.dry_run_evidence:
        parser.error("analysis requires --dry-run-evidence")
    if value.rollback_disposable and value.database.resolve() == LIVE_DATABASE:
        parser.error("--rollback-disposable refuses the live database")
    return value


def write_report(path: Path, report: dict[str, object]) -> Path:
    resolved = path.expanduser().resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        raise ValueError("Incident reports must be written outside the repository")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return resolved


def main() -> None:
    options = arguments()
    database = options.database.expanduser().resolve()
    source_checksum = sha256_file(database)
    if options.disposable_copy:
        database = disposable_copy(database, options.disposable_copy.expanduser().resolve())
    if options.rollback_disposable:
        report = rollback_incident_copy(database)
    elif options.recovery_plan:
        report = {
            "mode": "recovery-plan",
            "database": str(database),
            "source_checksum": source_checksum,
            "scope": recovery_scope(database),
            "database_writes": 0,
        }
    else:
        report = analyze_incident(database, options.dry_run_evidence)
    if options.output_json:
        write_report(options.output_json, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
