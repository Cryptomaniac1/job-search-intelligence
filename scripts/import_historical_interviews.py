#!/usr/bin/env python3
"""Replay historical email exports into Interview Pipeline evidence."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from backend.app.services.historical_interview_import import (  # noqa: E402
    HistoricalMessage,
    iter_mbox_messages,
    iter_yahoo_messages,
)

PROTECTED_DATABASES = {
    (ROOT / "data" / "jobs.db").resolve(),
    (ROOT / "backend" / "jobs.db").resolve(),
    (ROOT / "backend" / "jobs.db.migrated").resolve(),
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--gmail-mbox", type=Path, action="append", default=[])
    parser.add_argument("--hotmail-mbox", type=Path, action="append", default=[])
    parser.add_argument("--yahoo-json", type=Path, action="append", default=[])
    parser.add_argument(
        "--allow-live-database",
        action="store_true",
        help="Required in addition to a separate approval before using a protected runtime path.",
    )
    arguments = parser.parse_args()
    if not any((arguments.gmail_mbox, arguments.hotmail_mbox, arguments.yahoo_json)):
        parser.error("at least one historical source is required")
    return arguments


def verify_database(path: Path, *, allow_live_database: bool) -> None:
    resolved = path.resolve()
    if resolved in PROTECTED_DATABASES and not allow_live_database:
        raise SystemExit("Refusing protected runtime database without --allow-live-database")
    if not resolved.is_file():
        raise SystemExit(f"Database does not exist: {resolved}")
    uri = f"file:{resolved}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        required = {"imported_messages", "email_classifications", "interviews", "interview_events"}
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    if revision != ("20260712_0005",):
        raise SystemExit("Database must be at Alembic revision 20260712_0005")
    if not required.issubset(tables):
        raise SystemExit("Database is missing required Interview Pipeline tables")


def sources(arguments: argparse.Namespace) -> Iterable[tuple[str, Path, Iterable[HistoricalMessage]]]:
    for path in arguments.gmail_mbox:
        yield "gmail", path, iter_mbox_messages(path, "gmail")
    for path in arguments.hotmail_mbox:
        yield "hotmail", path, iter_mbox_messages(path, "hotmail")
    for path in arguments.yahoo_json:
        yield "yahoo", path, iter_yahoo_messages(path)


def main() -> None:
    arguments = parse_arguments()
    database = arguments.database.resolve()
    verify_database(database, allow_live_database=arguments.allow_live_database)
    os.environ["JOBS_DB_PATH"] = str(database)
    module = importlib.import_module("backend.main")
    results: list[dict[str, Any]] = []
    for provider, path, messages in sources(arguments):
        if not path.is_file():
            raise SystemExit(f"Historical source does not exist: {path}")
        result = module.import_historical_interview_messages(messages, source_name=path.name)
        result["provider"] = provider
        result["source"] = str(path.resolve())
        results.append(result)
    print(json.dumps({"database": str(database), "sources": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
