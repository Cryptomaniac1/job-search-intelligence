# Live Database Migration Runbook

## Status

This is the reviewed live-database migration procedure. Sprint 5.5 used it on 2026-07-12 to
upgrade `data/jobs.db` from `20260712_0002` to `20260712_0003` after an explicit approval gate.
The procedure remains guidance only for future migrations and never grants authorization by
itself.

Sprint 7.5 used this procedure on 2026-07-13 to upgrade the deployed database from
`20260712_0004` to `20260712_0005`. The Interview Pipeline tables were created empty, historical
counts and logical digests were preserved, and no import or backfill was performed.

## Sprint 5.5 deployment record

- Migration date: 2026-07-12.
- Source revision: `20260712_0002`.
- Result revision: `20260712_0003`.
- Verified backup:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-5-5/jobs-20260712T210406Z.sqlite3`.
- Backup metadata:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-5-5/jobs-20260712T210406Z.metadata.json`.
- Pre-migration and backup SHA-256:
  `3866fb03c9fa84c43c0aff0707fd9436c542fbe828339b3e669cb55a90343a08`.
- Post-migration SHA-256:
  `6ced7c832e04c00216b5da2784f3296a4b6586c79befac094d6a8ccf0af94a40`.
- Preserved counts: 7,718 `jobs`, four `email_imports`, zero `imported_messages`.
- New table count: zero `email_classifications`.
- The historical logical digests for `jobs`, `email_imports`, and `imported_messages` were
  unchanged.
- Schema, unique constraint, indexes, foreign keys, `integrity_check`, and `foreign_key_check`
  passed.
- Health, dashboard, and representative read-only job-list smoke tests returned HTTP 200.
- No import, backfill, cleanup, downgrade, or historical-row update was performed.
- Deviation: the first smoke-test port bind was denied by the execution sandbox; the same test
  passed after localhost permission was granted. This did not affect the database.

## Sprint 6.5 deployment record

- Migration date: 2026-07-12 local time / 2026-07-13 UTC.
- Source revision: `20260712_0003`.
- Result revision: `20260712_0004`.
- Verified backup:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-6-5/jobs-20260713T062008Z.sqlite3`.
- Backup metadata:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-6-5/jobs-20260713T062008Z.metadata.json`.
- Pre-migration SHA-256:
  `6ced7c832e04c00216b5da2784f3296a4b6586c79befac094d6a8ccf0af94a40`.
- Verified backup SHA-256:
  `7c3f4f52d241bc1b755abaeacf12022f18299ce4012991815fe8a54cc3c6ca76`.
- Post-migration SHA-256:
  `cb9376097110bf78f4b1540090688d8d063256d788525513814822b7df0592b3`.
- Preserved counts: 7,718 `jobs`, four `email_imports`, zero `imported_messages`, and zero
  `email_classifications`.
- New table counts: zero `recruiters`, `recruiter_company_links`,
  `recruiter_email_addresses`, and `recruiter_job_links`.
- Historical logical digests were unchanged.
- Schema, check/unique constraints, indexes, foreign keys, `integrity_check`, and
  `foreign_key_check` passed.
- Health, dashboard, representative read-only job-list, and recruiter-list smoke tests returned
  HTTP 200; the recruiter dashboard was present and the recruiter API returned an empty list.
- No import, recruiter extraction, backfill, cleanup, downgrade, or historical-row update was
  performed.

## Sprint 7.5 deployment record

- Migration date: 2026-07-13.
- Source revision: `20260712_0004`.
- Result revision: `20260712_0005`.
- Verified backup:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-7-5/jobs-20260713T074652Z.sqlite3`.
- Backup metadata:
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-7-5/jobs-20260713T074652Z.metadata.json`.
- Pre-migration SHA-256:
  `cb9376097110bf78f4b1540090688d8d063256d788525513814822b7df0592b3`.
- Verified backup SHA-256:
  `12ed1ae0f9bad91820938dfbc80acf11fe132981caef451c0a78d1623c11118b`.
- Post-migration SHA-256:
  `d2cfc342b4ac191618844bb46e85c44815af9ca0f2048d8a262bb554408d062b`.
- Preserved counts: 7,718 `jobs`, four `email_imports`, and zero `imported_messages`,
  `email_classifications`, `recruiters`, `recruiter_company_links`,
  `recruiter_email_addresses`, and `recruiter_job_links`.
- New table counts: zero `interviews` and zero `interview_events`.
- Historical logical digests were unchanged.
- The Interview Pipeline schema, check and unique constraints, indexes, and foreign keys were
  present and valid. `integrity_check` returned `ok`, and `foreign_key_check` returned no
  violations.
- Health, dashboard, representative read-only job-list, recruiter-list, and interview-list smoke
  tests returned HTTP 200; the recruiter and interview APIs returned empty lists.
- No import, interview or recruiter extraction, backfill, cleanup, downgrade, or historical-row
  update was performed.
- Operational warning: revision `20260712_0005` downgrade drops `interviews` and
  `interview_events`. Do not downgrade after either table contains evidence; use the verified
  backup or a separately approved evidence-preserving migration plan.

## Safety invariants

- Stop the application and all import processes before a live migration.
- Never operate without a verified external backup and its metadata file.
- Record the live checksum, size, schema, indexes, and row counts immediately before work.
- Preserve Yahoo, Hotmail, and Gmail account separation.
- Do not merge, delete, archive, or update existing duplicate records.
- Abort on any checksum, integrity, foreign-key, schema, or row-preservation discrepancy.

## 1. Backup

Choose an absolute directory outside the repository on storage with sufficient free space:

```bash
python -m backend.app.database.migration_readiness \
  backup data/jobs.db /absolute/external/job-intelligence-backups
```

The command opens the source in SQLite read-only/query-only mode, uses SQLite's online backup API,
then verifies the backup can be opened and queried. It writes:

- a timestamped `.sqlite3` backup;
- a `.metadata.json` evidence file containing checksum, size, schema, indexes, row counts,
  integrity results, foreign-key results, Alembic state, and UTC check time.

Retain both files. Do not place them in Git.

## 2. Read-only preflight

```bash
python -m backend.app.database.migration_readiness preflight
```

Proceed only when:

- `compatible` is `true`;
- state is `unversioned:baseline-compatible`;
- `jobs` and `email_imports` exist with expected columns;
- `ix_jobs_linkedin_job_id` is unique;
- integrity is `ok`;
- foreign-key violations are empty;
- row counts match the independently recorded baseline.

## 3. Rehearsal

```bash
python -m backend.app.database.migration_readiness \
  rehearse data/jobs.db /absolute/external/job-intelligence-rehearsal
```

The command creates another safe copy, stamps that copy at `20260712_0001`, upgrades it to
`20260712_0002`, repeats the upgrade to prove idempotence, validates constraints and logical row
digests, then downgrades to the baseline and validates again.

Do not proceed if the copy loses or changes any `jobs` or `email_imports` row.

## 4. Approval gate

Live deployment requires explicit approval after reviewing:

- verified backup and metadata paths;
- successful live read-only preflight;
- successful copy upgrade and rerun;
- sanitized Gmail, Hotmail, and Yahoo repeat-import results;
- successful rollback rehearsal;
- unchanged live checksum after all rehearsal work;
- maintenance window and recovery owner.

## 5. Historical live migration procedure

The block below is the original Sprint 2 template and intentionally retains its `NOT EXECUTED`
labels. Sprint 3 later established `20260712_0002` through a separately approved live procedure.
Do not rerun this archived template against the current live database.

```bash
# NOT EXECUTED — stop the backend and import processes first.
export JOBS_DB_PATH="$(pwd)/data/jobs.db"

# NOT EXECUTED — re-run and retain pre-migration evidence.
python -m backend.app.database.migration_readiness preflight "$JOBS_DB_PATH"
shasum -a 256 "$JOBS_DB_PATH"

# NOT EXECUTED — mark the existing two-table schema as the reviewed baseline.
python -m alembic stamp 20260712_0001

# NOT EXECUTED — create imported_messages and advance the revision.
python -m alembic upgrade 20260712_0002

# NOT EXECUTED — confirm the applied revision.
python -m alembic current
```

## 6. Post-migration validation — NOT EXECUTED

```bash
# NOT EXECUTED
python -m backend.app.database.migration_readiness preflight "$JOBS_DB_PATH"
sqlite3 -readonly "$JOBS_DB_PATH" "PRAGMA integrity_check; PRAGMA foreign_key_check;"
sqlite3 -readonly "$JOBS_DB_PATH" \
  "SELECT 'jobs', COUNT(*) FROM jobs
   UNION ALL SELECT 'email_imports', COUNT(*) FROM email_imports
   UNION ALL SELECT 'imported_messages', COUNT(*) FROM imported_messages;"
python -m alembic current
```

Expected immediately after migration:

- revision `20260712_0002`;
- unchanged `jobs` and `email_imports` counts and logical content;
- empty `imported_messages` until a new import occurs;
- expected unique identity constraint, provider/job indexes, and two foreign keys;
- integrity `ok` and no foreign-key violations.

Start the backend only after all checks pass. Perform a sanitized smoke import before enabling
normal imports.

## 7. Rollback — NOT EXECUTED

Rollback is acceptable only before valuable new provenance is created, or after separately backing
up that provenance. Downgrade drops `imported_messages`, permanently losing every identity,
source-import link, job link, outcome, error, and import timestamp stored there.

```bash
# NOT EXECUTED — stop backend/import processes and back up first.
export JOBS_DB_PATH="$(pwd)/data/jobs.db"
python -m alembic downgrade 20260712_0001
python -m alembic current
python -m backend.app.database.migration_readiness preflight "$JOBS_DB_PATH"
```

The downgrade must preserve `jobs` and `email_imports`. It does not undo job changes made by imports
after migration; restoring the verified pre-migration backup is safer if post-migration imports ran.

## 8. Recovery from failed migration

1. Keep the backend and all import processes stopped.
2. Capture the failed database, logs, checksum, schema, and Alembic revision for diagnosis.
3. Do not rerun stamp, upgrade, or downgrade blindly.
4. If no post-backup data must be retained, move the failed database aside and restore the verified
   backup to the original path using an operator-approved filesystem procedure.
5. Verify the restored checksum against backup metadata, then run preflight and SQLite integrity
   checks.
6. If post-backup data must be retained, stop and design a separate evidence-preserving recovery;
   do not merge databases manually.
7. Restart only after schema, checksum expectations, row counts, integrity, and application startup
   have been verified.

## Runtime storage relocation

The live runtime database now defaults to `data/jobs.db`. `JOBS_DB_PATH` remains the highest
priority override and `DATABASE_PATH` is secondary. The legacy source is retained temporarily as
ignored, untracked `backend/jobs.db.migrated`.

If `data/jobs.db` is missing or corrupt, keep the backend stopped. Preserve the failed file, verify
the durable external backup and its metadata, restore the backup to `data/jobs.db`, run the
readiness preflight, confirm revision and logical digests, then perform read-only smoke tests.
Never silently fall back to the legacy file.
