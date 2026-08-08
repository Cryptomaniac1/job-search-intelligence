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

### `email_classifications`

Added by revision `20260712_0003`. Stores additive, immutable classifier evidence keyed by stable
message identity and classifier version:

- canonical classification;
- confidence from 0.0 to 1.0;
- deterministic classifier version;
- JSON reasons explaining matched signals;
- optional existing job linkage;
- creation timestamp.

The unique `(message_identity, classifier_version)` constraint permits future classifier versions
without overwriting earlier evidence. Sprint 5 performs no historical backfill.

### Recruiter CRM tables

Revision `20260712_0004` adds `recruiters`, `recruiter_company_links`,
`recruiter_email_addresses`, and `recruiter_job_links`. These tables store deterministic recruiter
profiles, normalized company/email matching evidence, and explicit recruiter-to-job relationships.
The relationship table preserves its first source message and updates only observation timestamps
when the same recruiter, job, and relationship type is seen again.

The migration is additive and does not alter `jobs` or backfill historical messages. The live
runtime database was upgraded to `20260712_0004` through the approval-gated Sprint 6.5 workflow.
Historical row counts and logical digests were preserved, and all four Recruiter CRM tables began
with zero rows.

### Interview Pipeline tables

Revision `20260712_0005` adds `interviews` and `interview_events`. `interviews` is the current
aggregate schedule/status for a deterministically linked job. `interview_events` is immutable,
versioned source-message evidence and may remain unlinked when no job can be identified safely.

Events preserve classification linkage, provider/account, parsed schedule and location values,
extractor version, matched signals, ambiguity reasons, and source-message identity. A unique
`(source_message_identity, extractor_version)` constraint makes repeat imports idempotent while
allowing future extractor versions to add evidence. Reschedules and cancellations update the
aggregate without deleting earlier events. Assessments use `interview_type=assessment`.

The migration is additive and performs no historical backfill. On 2026-07-13, the live database
was upgraded from `20260712_0004` to `20260712_0005` through the approval-gated Sprint 7.5
workflow. Historical row counts and logical digests were preserved, and `interviews` and
`interview_events` began with zero rows.

Downgrading after interview evidence has been written would drop both Interview Pipeline tables
and destroy that evidence. After either table contains data, use a verified backup or a separately
reviewed evidence-preserving migration plan instead of downgrading.

### Yahoo IMAP synchronization tables

Revision `20260712_0006` additively creates `imap_sync_checkpoints` and
`imap_message_metadata`. Checkpoints are unique by provider, normalized account namespace, exact
folder, and requested `since_date`. They record UIDVALIDITY, the last successfully processed UID,
run timestamps, and scanned/accepted/skipped/failure counts. This keeps different date-bounded
scopes independent while retaining UID-based incremental progress. A changed UIDVALIDITY is never
accepted automatically.

Message metadata preserves the stable transport identity, original normalized message fields,
recipients, folder, UIDVALIDITY, UID, the Yahoo IMAP internal date, sender-provided `Date` header,
requested since-date, HTML-fallback marker, and attachment metadata without attachment bodies. It
references immutable `imported_messages` provenance and is unique both by message identity and
provider/account/folder/UID namespace.

The migration does not backfill or modify historical rows. On 2026-07-13 local time / 2026-07-14
UTC, the live database was approval-gated and upgraded to `20260712_0006`. Historical counts and
logical digests were preserved; both new tables began with zero rows. Its verified post-migration
SHA-256 is `088e96d7d518815ef5b1de757a6e7d6aaff9695b9d4706f8d25602952c4a91b0`.

### Sprint 10 Yahoo incident state

The first bounded Yahoo production attempt fetched UIDs `53293` through `53392` without mailbox
mutation. The persistence pass accepted 94 messages. The former idempotency implementation then
called the normal importer again, accepted UID `53319`, and created a second `email_imports` row.
It stopped before writing a checkpoint. The live database therefore intentionally remains at
revision `20260712_0006` with checksum
`e82d1fa0e4e751ec14b36cf82298e0931c81631698704c0d1152bae7bfe52bc1`, 7,737 jobs, six import
rows, and 95 rows in each of `imported_messages`, `email_classifications`, and
`imap_message_metadata`. All recruiter and interview tables and `imap_sync_checkpoints` remain
empty. Integrity and foreign-key checks pass.

The original 7,718 job rows and four import rows exactly match the verified pre-migration backup.
Do not restore, retry, or advance the checkpoint without the separately approved incident
recovery gate. The SQLite-safe incident backup and sanitized evidence are external to Git under
`~/Documents/job-intelligence-backups/sprint-10-1-idempotency-incident/`.

## Migration history

- `20260712_0001`: baseline representation of `jobs` and `email_imports`.
- `20260712_0002`: additive imported-message identity and provenance table.
- `20260712_0003`: deterministic email classification evidence.
- `20260712_0004`: deterministic Recruiter CRM foundation and job relationships.
- `20260712_0005`: deterministic Interview Pipeline aggregates and immutable event evidence.
- `20260712_0006`: Yahoo IMAP checkpoints and immutable UID transport metadata.
- `20260808_0007`: additive Version 1 product records for companies, resumes, applications,
  job descriptions, offers, recruiter relationships, notes, and interactions.

The live runtime database was upgraded from `20260712_0002` to `20260712_0003` on 2026-07-12
through the approval-gated live-migration workflow. The migration preserved 7,718 `jobs` rows,
four `email_imports` rows, zero `imported_messages` rows, and the logical digests of all three
existing tables. `email_classifications` was created with zero rows. No historical backfill or
email import was run.

The live runtime database was upgraded from `20260712_0004` to `20260712_0005` on 2026-07-13.
The verified pre-migration backup and metadata are
`/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-7-5/jobs-20260713T074652Z.sqlite3`
and
`/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-7-5/jobs-20260713T074652Z.metadata.json`.
The live SHA-256 changed from
`cb9376097110bf78f4b1540090688d8d063256d788525513814822b7df0592b3` to
`d2cfc342b4ac191618844bb46e85c44815af9ca0f2048d8a262bb554408d062b` as the additive schema was
applied. Counts remained 7,718 `jobs`, four `email_imports`, and zero rows in every existing
evidence and Recruiter CRM table. Historical logical digests were unchanged; the two new tables
began empty. Schema, check and unique constraints, indexes, foreign keys, `integrity_check`, and
`foreign_key_check` passed. Read-only health, dashboard, job-list, recruiter-list, and
interview-list smoke tests returned HTTP 200. No import, extraction, backfill, cleanup, downgrade,
or historical-row update was performed.

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

## Version 1 product closeout schema

Revision `20260808_0007` additively creates `companies`, `resumes`, `applications`,
`job_descriptions`, `offers`, `recruiter_relationships`, `notes`, and `interactions`. It does not
rewrite or backfill legacy `jobs` rows or immutable email, recruiter, or interview evidence.
Foreign keys link new records to existing entities when the relationship is explicit. Unique and
check constraints prevent duplicate job applications and invalid lifecycle values.

The migration is reversible on a disposable database, but downgrading after any Version 1 product
record exists would destroy those new records. Use a verified backup or a separately reviewed
evidence-preserving migration plan instead.

On 2026-08-08, the ignored runtime database was approval-gated and upgraded from
`20260712_0006` to `20260808_0007`. The pre-existing table counts and logical digests were
preserved by the migration, all eight new tables began empty, `integrity_check` returned `ok`, and
`foreign_key_check` returned no violations. Subsequent approved bounded provider imports were
additive and intentionally advanced the live evidence state.

After Yahoo recovery and two bounded production passes each for Gmail and Hotmail, the verified
runtime state is 7,750 `jobs`, 10 `email_imports`, 297 each of `imported_messages`,
`email_classifications`, and `imap_message_metadata`, two recruiters, 12 interview events, and
three provider checkpoints. The Gmail and Hotmail repeat passes added zero messages. The final
SHA-256 is `382c42c9a7e1a104baf8c854c3eb3c76cd0b46210920fee505c882358d030367`.
The verified final backup and metadata are:

- `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-12/final/jobs-20260808T214439Z.sqlite3`
- `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-12/final/jobs-20260808T214439Z.metadata.json`

Application, Email, Recruiter, Company, Job, Interview, Resume, and Offer remain the canonical
domain vocabulary described in `docs/specification/DOMAIN_MODEL.md`. Sprint 12 implements the
minimum additive persistence needed for daily Version 1 use; richer intelligence remains planned.
