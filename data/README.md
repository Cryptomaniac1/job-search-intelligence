# Runtime Data

`data/jobs.db` is the default local runtime database. It is intentionally ignored by Git.

Path resolution order:

1. `JOBS_DB_PATH`
2. `DATABASE_PATH`
3. repository default `data/jobs.db`

Relative override paths resolve from the repository root. On first startup, a missing resolved
database gets its parent directory created and is initialized to the current Alembic revision. An
existing database is never overwritten or recreated. The application never falls back to the
legacy `backend/jobs.db` path.

Back up runtime data outside the repository. See `docs/LIVE_DATABASE_MIGRATION_RUNBOOK.md`.
