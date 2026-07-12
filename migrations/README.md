# Migration Safety

The baseline revision models `jobs` and `email_imports`. Revision `20260712_0002` adds the narrowly
scoped `imported_messages` provenance table.

- Use `JOBS_DB_PATH` with a disposable database for development and automated verification.
- Never point automated migration commands at `backend/jobs.db`.
- Existing databases with the baseline schema are recognized through an explicit
  `alembic stamp 20260712_0001` after schema verification and backup.
- Application startup does not invoke Alembic.

See `DEVELOPER_GUIDE.md` for commands and the live-database protection policy.
