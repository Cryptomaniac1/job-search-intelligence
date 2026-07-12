from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_application_uses_database_path_secondary_override(tmp_path: Path) -> None:
    database_path = tmp_path / "app" / "secondary.db"
    environment = os.environ.copy()
    environment.pop("JOBS_DB_PATH", None)
    environment["DATABASE_PATH"] = str(database_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.main import DB_PATH; print(DB_PATH)",
        ],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip().endswith(str(database_path))
    assert database_path.exists()


def test_readiness_cli_defaults_to_resolved_override(tmp_path: Path) -> None:
    database_path = tmp_path / "readiness.db"
    environment = os.environ.copy()
    environment["JOBS_DB_PATH"] = str(database_path)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.app.database.migration_readiness",
            "preflight",
        ],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert str(database_path.resolve()) in result.stdout
