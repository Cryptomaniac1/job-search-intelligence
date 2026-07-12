from __future__ import annotations

from pathlib import Path

import pytest
from backend.app.database.paths import (
    DEFAULT_DATABASE_PATH,
    REPOSITORY_ROOT,
    ensure_parent_directory,
    initialize_database_if_missing,
    resolve_database_path,
)


def test_default_path_is_repository_data_database() -> None:
    assert resolve_database_path({}) == DEFAULT_DATABASE_PATH
    assert DEFAULT_DATABASE_PATH == (REPOSITORY_ROOT / "data" / "jobs.db").resolve()
    assert DEFAULT_DATABASE_PATH != (REPOSITORY_ROOT / "backend" / "jobs.db").resolve()


def test_jobs_db_path_has_highest_precedence(tmp_path: Path) -> None:
    jobs_path = tmp_path / "jobs-override.db"
    database_path = tmp_path / "database-override.db"

    resolved = resolve_database_path(
        {"JOBS_DB_PATH": str(jobs_path), "DATABASE_PATH": str(database_path)}
    )

    assert resolved == jobs_path.resolve()


def test_database_path_is_secondary_override(tmp_path: Path) -> None:
    database_path = tmp_path / "database-override.db"

    assert resolve_database_path({"DATABASE_PATH": str(database_path)}) == database_path.resolve()


def test_relative_override_is_absolute_and_repository_relative() -> None:
    resolved = resolve_database_path({"DATABASE_PATH": "scratch/runtime.db"})

    assert resolved.is_absolute()
    assert resolved == (REPOSITORY_ROOT / "scratch" / "runtime.db").resolve()


def test_parent_directory_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "runtime.db"

    resolved = ensure_parent_directory(database_path)

    assert resolved.parent.is_dir()
    assert not resolved.exists()


def test_missing_database_is_initialized_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "new" / "runtime.db"
    monkeypatch.chdir(REPOSITORY_ROOT / "backend")

    created = initialize_database_if_missing(database_path)
    created_again = initialize_database_if_missing(database_path)

    assert created
    assert not created_again
    assert database_path.exists()
