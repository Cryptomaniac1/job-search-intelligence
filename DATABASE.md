# Database

## Implemented schema

SQLite is the current persistence engine. The default live runtime database is `data/jobs.db`.
Runtime databases are ignored by Git and must never be staged or committed.

Canonical path resolution is:

1. `JOBS_DB_PATH`;
2. `DATABASE_PATH`;
3. repository default `data/jobs.db`.

Relative overrides resolve from the repository root, and the result is absolute before access.
There is no fallback to `backend/jobs.db`.

### `jobs`

Stores the current combined job-discovery and application-tracking record. Sprint 1 does not split
this legacy model. Important import fields include `email_account`, `role_family`, `resume_family`,
`applied_at`, `confirmation_message_id`, `ats_platform`, `requisition_id`,
`application_source`, and `import_confidence`.

### `email_imports`

Stores one summary row for every MBOX or Yahoo JSON import attempt, including source filename,
provider, message totals, confirmation totals, and matched/unmatched totals. Repeat attempts create
new summary rows so import runs remain auditable.

### `imported_messages`

Added by Alembic revision `20260712_0002`. Stores one immutable provenance row for each unique
provider-scoped message:

- `provider`: normalized Gmail, Hotmail, or Yahoo account namespace.
- `source_import_id`: the first `email_imports` run that accepted the message.
- `stable_message_identity`: unique versioned SHA-256 identity.
- `original_message_id`: original RFC Message-ID when available.
- `imported_at`: first accepted import time.
- `job_id`: matched or newly created legacy job.
- `outcome`: `matched`, `unmatched`, or `failed`.
- `error`: failure detail when applicable.

The unique identity prevents duplicate message and job creation. Later repeat attempts are counted
as `already_imported` in their `email_imports` summary and do not overwrite the provenance row.

## Migration history

- `20260712_0001`: baseline representation of `jobs` and `email_imports`.
- `20260712_0002`: additive imported-message identity and provenance table.

Application startup does not run Alembic. See `DEVELOPER_GUIDE.md` for safe temporary upgrade and
existing-database stamping procedures.

## Migration readiness

`backend.app.database.migration_readiness` provides protected operational commands:

- `preflight` opens SQLite in read-only and query-only modes and validates schema, columns,
  indexes, row readability, integrity, foreign keys, and Alembic state.
- `backup` uses SQLite's online backup API and writes the database plus JSON evidence outside the
  repository.
- `rehearse` backs up, stamps, upgrades twice, validates row digests and constraints, downgrades,
  and validates the baseline again using only the copy.
- `duplicate-report` writes a field-level review CSV without changing source records.

All Alembic operations in the readiness tool reject both the current live path and the legacy
source path. Automated tests use only temporary databases.

## Storage relocation

On 2026-07-12, the revision `20260712_0002` live database was copied with SQLite's online backup
API from `backend/jobs.db` to `data/jobs.db`. Source and destination had identical checksums,
schema, indexes, constraints, foreign keys, row counts, and logical digests. Read-only application
smoke tests passed against the new default.

The old source is retained locally as `backend/jobs.db.migrated`; it is ignored and untracked and
must not be deleted during Sprint 4.

When a resolved database does not exist, startup creates its parent directory and upgrades a new
database through Alembic to the current revision. Existing databases are never overwritten,
recreated, or silently replaced.

## Target architecture

Application, Email, Recruiter, Company, Job, Interview, Resume, and Offer are target domain
concepts described in `docs/specification/DOMAIN_MODEL.md`. Except for the legacy `jobs` table,
Sprint 1 does not create tables for those concepts. Their persistence design remains planned.
