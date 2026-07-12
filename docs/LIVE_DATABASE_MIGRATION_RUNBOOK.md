# Live Database Migration Runbook

## Status

This is a reviewed procedure, not authorization to migrate `data/jobs.db`. Sprint 2 executed
backup, preflight, upgrade, import, and rollback rehearsals only against SQLite-safe copies. The
live migration commands below are marked **NOT EXECUTED** and require separate explicit approval.

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

## 5. Live migration procedure — NOT EXECUTED

The following commands were **not executed against `backend/jobs.db` in Sprint 2**. The readiness
tool intentionally rejects that path, so live deployment uses direct Alembic only after explicit
approval and a verified backup.

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
