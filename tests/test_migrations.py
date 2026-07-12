from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_baseline_migration_builds_current_two_table_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert tables == {"alembic_version", "email_imports", "jobs"}
    assert revision == ("20260712_0001",)
