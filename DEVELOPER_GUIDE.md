# Developer Guide

## Local setup

Python 3.12 is the supported version. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

The checked-in startup wrapper remains compatible:

```bash
./start_backend.sh
```

It starts the existing application as `main:app` from the `backend` directory on
`http://127.0.0.1:8002`. The import path `backend.main:app` also remains supported when running
from the repository root:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8002
```

By default, the application uses `backend/jobs.db`. `JOBS_DB_PATH` exists only to direct tests and
explicit development commands to a disposable database; production startup should leave it unset.

## Verification commands

Run these commands from the repository root:

```bash
pytest
ruff check backend/app tests
black --check backend/app tests
mypy backend/app tests
```

The initial tooling scope deliberately excludes `backend/main.py`. New package and test code must
pass all checks. Formatting and fully typing the legacy monolith is later refactoring work and must
not be mixed into unrelated changes.

## Temporary-database testing policy

Automated tests must set `JOBS_DB_PATH` to a path created by Pytest's `tmp_path` fixture before
importing `backend.main`. Tests must never copy, open through the application, migrate, stamp, or
write to `backend/jobs.db`.

Before and after database-related work, record the historical database checksum, schema, and row
counts with read-only SQLite access. A changed checksum requires investigation before delivery.

## Alembic

The baseline revision is `20260712_0001`. It represents the two implemented application tables:
`jobs` and `email_imports`. It does not introduce target-domain tables.

Create and upgrade a disposable database:

```bash
JOBS_DB_PATH=/tmp/job-intelligence-migration.db alembic upgrade head
JOBS_DB_PATH=/tmp/job-intelligence-migration.db alembic current
```

For an existing database that already has both baseline tables, first verify its schema against the
baseline and back it up. Then explicitly mark it as current without running the baseline DDL:

```bash
JOBS_DB_PATH=/absolute/path/to/existing.db alembic stamp 20260712_0001
```

`stamp` adds or updates Alembic's revision marker; it does not recreate `jobs` or `email_imports` or
change their data rows. It must be an intentional operator action. Application startup never runs
Alembic and never stamps a database automatically.

Do not run `alembic upgrade`, `alembic downgrade`, or `alembic stamp` against
`backend/jobs.db` without explicit authorization, a verified backup, and before/after evidence.

## Pre-commit

Optional local hooks use the same initial scope:

```bash
pre-commit install
pre-commit run --all-files
```

## Releases

- Update the existing canonical documentation.
- Run the verification commands above.
- Verify migrations against a temporary database.
- Validate existing API and browser-extension compatibility.
- Reconfirm the historical database checksum, schema, and row counts.
