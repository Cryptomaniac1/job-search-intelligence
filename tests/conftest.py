from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def isolated_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Path]]:
    """Load the legacy app against a new temporary database."""
    database_path = tmp_path / "test-jobs.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(database_path))
    sys.modules.pop("backend.main", None)

    module = importlib.import_module("backend.main")
    with TestClient(module.app) as client:
        yield client, database_path

    module.engine.dispose()
    sys.modules.pop("backend.main", None)
    os.environ.pop("JOBS_DB_PATH", None)
