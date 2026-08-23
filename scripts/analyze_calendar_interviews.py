#!/usr/bin/env python3
"""Create a content-free monthly interview count from a local ICS export."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.calendar_analytics import analyze_calendar_interviews  # noqa: E402


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ics", type=Path)
    parser.add_argument("--from-date", type=_date, required=True)
    parser.add_argument("--through-date", type=_date, default=date.today())
    parser.add_argument("--timezone", default="America/Los_Angeles")
    parser.add_argument("--output-json", type=Path)
    arguments = parser.parse_args()
    result = analyze_calendar_interviews(
        arguments.ics,
        from_date=arguments.from_date,
        through_date=arguments.through_date,
        local_timezone=arguments.timezone,
    )
    output = json.dumps(result, indent=2) + "\n"
    if arguments.output_json:
        arguments.output_json.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
