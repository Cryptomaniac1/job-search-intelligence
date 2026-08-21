#!/usr/bin/env python3
"""Reconcile a legacy local LinkedIn Applied ledger into a runtime database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.linkedin_submission_ledger import (  # noqa: E402
    reconcile_legacy_linkedin_submissions,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    report = reconcile_legacy_linkedin_submissions(args.source_database, args.database).to_dict()
    result = {
        "source_database": str(args.source_database.resolve()),
        "database": str(args.database.resolve()),
        "report": report,
    }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
