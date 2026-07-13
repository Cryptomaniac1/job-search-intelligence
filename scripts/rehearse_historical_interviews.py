#!/usr/bin/env python3
"""Safely rehearse historical interview replay on a disposable SQLite copy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from backend.app.services.historical_interview_rehearsal import (  # noqa: E402
    ProviderInput,
    run_rehearsal,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--gmail-mbox", type=Path, action="append", default=[])
    parser.add_argument("--hotmail-mbox", type=Path, action="append", default=[])
    parser.add_argument("--yahoo-json", type=Path, action="append", default=[])
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete only the generated run directory after printing the evidence summary.",
    )
    arguments = parser.parse_args()
    if not any((arguments.gmail_mbox, arguments.hotmail_mbox, arguments.yahoo_json)):
        parser.error("at least one provider input is required")
    return arguments


def provider_inputs(arguments: argparse.Namespace) -> list[ProviderInput]:
    inputs = [ProviderInput("gmail", path) for path in arguments.gmail_mbox]
    inputs.extend(ProviderInput("hotmail", path) for path in arguments.hotmail_mbox)
    inputs.extend(ProviderInput("yahoo", path) for path in arguments.yahoo_json)
    return inputs


def main() -> None:
    arguments = parse_arguments()
    try:
        evidence = run_rehearsal(
            source_database=arguments.source_database,
            output_directory=arguments.output_directory,
            inputs=provider_inputs(arguments),
            repository_root=ROOT,
            cleanup=arguments.cleanup,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Rehearsal refused: {exc}") from exc
    print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    if not evidence.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
