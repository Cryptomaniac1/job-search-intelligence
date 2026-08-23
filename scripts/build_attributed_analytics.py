#!/usr/bin/env python3
"""Build an aggregate analytics snapshot from operator-supplied evidence files."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.attributed_analytics import build_attributed_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-plan", type=Path, required=True)
    parser.add_argument("--funnel-analysis", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--linkedin-submission-ledger",
        type=Path,
        help="Optional legacy scanner database containing dated LinkedIn Applied records.",
    )
    parser.add_argument("--through-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = (args.application_plan, args.funnel_analysis, args.calendar, args.database)
    if args.linkedin_submission_ledger:
        sources += (args.linkedin_submission_ledger,)
    for source in sources:
        if not source.is_file():
            parser.error(f"source does not exist: {source}")
    result = build_attributed_snapshot(
        args.application_plan,
        args.funnel_analysis,
        args.calendar,
        args.database,
        through_date=args.through_date,
        linkedin_submission_ledger_path=args.linkedin_submission_ledger,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "summary": result["funnel"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
