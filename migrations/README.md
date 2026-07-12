# Migration Safety

The baseline revision models `jobs` and `email_imports`. Revision `20260712_0002` adds the narrowly
scoped `imported_messages` provenance table.

- Use `JOBS_DB_PATH` with a disposable database for development and automated verification.
- Never point automated migration commands at `backend/jobs.db`.
- Existing databases with the baseline schema are recognized through an explicit
  `alembic stamp 20260712_0001` after schema verification and backup.
- Application startup does not invoke Alembic.
- The migration-readiness tool refuses to run Alembic against the resolved historical database.
- Backup and rehearsal outputs must be outside the repository and are never committed.
- Downgrading `20260712_0002` drops `imported_messages` and permanently loses any provenance rows
  stored there; it does not remove `jobs` or `email_imports`.

See `DEVELOPER_GUIDE.md` for commands and `docs/LIVE_DATABASE_MIGRATION_RUNBOOK.md` for the gated
deployment and recovery procedure.
