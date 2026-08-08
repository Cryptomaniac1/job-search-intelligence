# Migration Safety

The baseline revision models `jobs` and `email_imports`. Revision `20260712_0002` adds the narrowly
scoped `imported_messages` provenance table.
Revision `20260712_0003` adds versioned deterministic `email_classifications` evidence.
Revision `20260712_0004` adds the deterministic Recruiter CRM foundation and explicit job links.
Revision `20260712_0005` adds deterministic interview aggregates and immutable event evidence.
Revision `20260712_0006` adds date-scope-aware Yahoo IMAP checkpoints and immutable UID transport
metadata, including IMAP internal-date and requested-since-date audit fields.
Revision `20260808_0007` additively adds companies, resumes, applications, job descriptions,
offers, recruiter relationships, notes, and interactions for Version 1 product closeout.

- The default target is `data/jobs.db`; `JOBS_DB_PATH` and then `DATABASE_PATH` override it.
- Use an override with a disposable database for development and automated verification.
- Never point automated migration commands at `data/jobs.db` or `backend/jobs.db.migrated`.
- Existing databases with the baseline schema are recognized through an explicit
  `alembic stamp 20260712_0001` after schema verification and backup.
- Application startup does not invoke Alembic.
- Sprint 7 verifies `20260712_0004` to `20260712_0005`, repeat upgrade, downgrade to `0004`, and
  re-upgrade using temporary databases only. The live database is not migrated during feature work.
- Sprint 9 verifies `20260712_0005` to `20260712_0006`, downgrade to `0005`, and re-upgrade using
  temporary databases only. The live database remains at `0005` during feature work.
- Sprint 12 verifies `20260712_0006` to `20260808_0007`, downgrade to `0006`, and re-upgrade using
  temporary databases only. The live database remains at `0006` during feature work.
- The migration-readiness tool refuses to run Alembic against current and legacy protected paths.
- Backup and rehearsal outputs must be outside the repository and are never committed.
- Downgrading `20260712_0002` drops `imported_messages` and permanently loses any provenance rows
  stored there; it does not remove `jobs` or `email_imports`.

See `DEVELOPER_GUIDE.md` for commands and `docs/LIVE_DATABASE_MIGRATION_RUNBOOK.md` for the gated
deployment and recovery procedure.
