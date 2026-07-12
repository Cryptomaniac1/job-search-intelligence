"""Canonical runtime database path resolution and safe initialization."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = (REPOSITORY_ROOT / "data" / "jobs.db").resolve()
LEGACY_DATABASE_PATH = (REPOSITORY_ROOT / "backend" / "jobs.db").resolve()


def resolve_database_path(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve JOBS_DB_PATH, DATABASE_PATH, or the repository default in priority order."""
    values = os.environ if environment is None else environment
    configured = values.get("JOBS_DB_PATH") or values.get("DATABASE_PATH")
    if not configured:
        return DEFAULT_DATABASE_PATH
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def ensure_parent_directory(path: Path) -> Path:
    """Create only the resolved database's parent directory."""
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def initialize_database_if_missing(path: Path) -> bool:
    """Upgrade a new database to Alembic head without touching an existing file."""
    resolved = ensure_parent_directory(path)
    if resolved.exists():
        return False

    from alembic import command
    from alembic.config import Config

    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(REPOSITORY_ROOT))
    config.attributes["database_path"] = resolved
    command.upgrade(config, "head")
    return True
