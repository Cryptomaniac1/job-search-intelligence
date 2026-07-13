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

By default, the application uses `data/jobs.db`. Resolution priority is `JOBS_DB_PATH`, then
`DATABASE_PATH`, then the repository default. Relative overrides resolve from the repository root.
The existing startup commands require no changes.

Examples:

```bash
JOBS_DB_PATH=/absolute/path/to/jobs.db ./start_backend.sh
DATABASE_PATH=/absolute/path/to/jobs.db uvicorn backend.main:app --host 127.0.0.1 --port 8002
```

If the resolved database is missing, startup creates its parent directory and initializes a new
database at Alembic head. It never overwrites an existing file or falls back to `backend/jobs.db`.

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

Pull requests targeting `main` run the same checks in GitHub Actions, plus migration
downgrade/re-upgrade verification, JavaScript syntax checks, repository database guards, and
temporary-database smoke tests. See `docs/CI_AND_PULL_REQUEST_AUTOMATION.md` for the required check
names and the manual GitHub protection/review setup.

## Temporary-database testing policy

Automated tests must set `JOBS_DB_PATH` or `DATABASE_PATH` to a path created by Pytest's `tmp_path`
fixture before importing `backend.main`. Tests must never copy, open through the application,
migrate, stamp, or write to `data/jobs.db` or `backend/jobs.db.migrated`.

Before and after database-related work, record the historical database checksum, schema, and row
counts with read-only SQLite access. A changed checksum requires investigation before delivery.

## Alembic

The baseline revision is `20260712_0001`. Revision `20260712_0002` adds imported-message identity
and provenance. Revision `20260712_0003` adds deterministic classification evidence. Revision
`20260712_0004` adds the Recruiter CRM foundation without changing historical job rows or creating
interview and offer entities. Revision `20260712_0005` adds interview aggregates and immutable
event evidence without backfill. The deployed live database remains at `20260712_0004` until a
separate approved migration.

The live runtime database remains at its currently deployed revision until an explicitly approved
copy rehearsal and live migration. Feature development and tests never upgrade `data/jobs.db`.

Create and upgrade a disposable database:

```bash
JOBS_DB_PATH=/tmp/job-intelligence-migration.db alembic upgrade head
JOBS_DB_PATH=/tmp/job-intelligence-migration.db alembic current
```

For an existing database that already has both baseline tables, first verify its schema against the
baseline and back it up. Then explicitly mark it as current without running the baseline DDL:

```bash
JOBS_DB_PATH=/absolute/path/to/existing.db alembic stamp 20260712_0001
JOBS_DB_PATH=/absolute/path/to/existing.db alembic upgrade 20260712_0002
```

`stamp` adds or updates Alembic's revision marker; it does not recreate `jobs` or `email_imports` or
change their data rows. It must be an intentional operator action. Application startup never runs
Alembic and never stamps a database automatically.

The `20260712_0002` upgrade is additive, but it must still be run only after backup and schema
verification. It creates `imported_messages`; it does not backfill, merge, or delete historical
rows.

## Migration-readiness commands

Run the read-only live preflight using the canonical default:

```bash
python -m backend.app.database.migration_readiness preflight
```

Create a SQLite-safe backup outside the repository:

```bash
python -m backend.app.database.migration_readiness \
  backup data/jobs.db /absolute/external/backup-directory
```

Run stamp, upgrade, rerun, validation, and rollback against a generated copy only:

```bash
python -m backend.app.database.migration_readiness \
  rehearse data/jobs.db /absolute/external/rehearsal-directory
```

Generate the full duplicate-candidate report outside the repository:

```bash
python -m backend.app.database.migration_readiness \
  duplicate-report data/jobs.db /absolute/external/duplicate-candidates.csv
```

Backup metadata records checksum, size, schema, indexes, row counts, integrity, foreign-key
results, Alembic state, and UTC creation/check time. A SQLite online backup may have a different
file checksum from its source because page layout can change; preservation is verified using table
row counts and deterministic logical row digests.

See `docs/LIVE_DATABASE_MIGRATION_RUNBOOK.md` before proposing any live deployment.

## Temporary Interview Pipeline demo

Run the sanitized dashboard demonstration without touching the runtime database:

```bash
backend/.venv/bin/python scripts/start_interview_demo.py
```

The command creates a database under the operating system temporary directory, upgrades it to
Alembic head, creates one sanitized job, imports the checked-in interview fixtures, prints the
dashboard URL, and deletes the temporary database when stopped. Use `--prepare-only` for a
non-network isolation check. It always overrides `JOBS_DB_PATH` and never opens `data/jobs.db`.

## Import identity and merge policy

Message identity is provider-scoped. RFC Message-ID values are Unicode-normalized, case-folded,
trimmed, stripped of whitespace and surrounding angle brackets, then hashed with the normalized
provider. Without Message-ID, the fingerprint hashes normalized provider, subject, sender, parsed
date, and body. Text uses Unicode NFKC normalization, case-folding, whitespace collapse, and trim.
Canonical sorted JSON is hashed with SHA-256 and prefixed with `v1:`.

Yahoo structured application records retain their legacy identity inputs. When the additive raw
`subject`, `sender`, or `body` fields are present, those message fields drive provider-scoped
identity, classification, and downstream deterministic evidence processing.

On a matched job, import values fill missing fields only. Existing account, role family, resume
family, applied date, Message-ID, ATS, requisition ID, application source, URL, company, and title
are preserved. Existing source, status, and notes are never replaced by an import. Confidence may
only increase. Jobs already assigned to another email account are excluded from matching.

Do not run `alembic upgrade`, `alembic downgrade`, or `alembic stamp` against `data/jobs.db`
without explicit authorization, a verified backup, and before/after evidence.

## Restore and legacy handling

Restore only while the backend is stopped. Verify an external backup first, preserve the failed
database for diagnosis, copy the verified backup to `data/jobs.db`, then run preflight before
restart. Never restore into `backend/jobs.db`.

`backend/jobs.db.migrated` is the verified pre-relocation source retained temporarily for rollback.
It is ignored, untracked, and must not be committed or deleted without separate approval.

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
