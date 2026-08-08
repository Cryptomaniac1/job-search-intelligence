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
event evidence without backfill.

Revision `20260712_0006` adds Yahoo IMAP checkpoints and immutable UID transport metadata. It is
deployed on the live database following the approval-gated Sprint 10 rehearsal and migration.

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
Alembic head, creates one sanitized job, replays the checked-in interview fixtures through the
historical importer, prints the dashboard URL, and deletes the temporary database when stopped.
Use `--prepare-only` for a non-network isolation check. It always overrides `JOBS_DB_PATH` and
never opens `data/jobs.db`.

## Historical interview replay

Historical replay reads complete Gmail/Hotmail MBOX or Yahoo raw-message JSON exports but stores
only unambiguous messages matching the supported deterministic interview and assessment event
types. It never invokes application matching/creation and never changes a `jobs` row. Existing
message provenance and classifications are reused without updates; missing provenance and
classification records are added only for accepted interview evidence.

Run against an existing disposable database already upgraded to `20260712_0005`:

```bash
backend/.venv/bin/python scripts/import_historical_interviews.py \
  --database /absolute/path/to/temporary.db \
  --gmail-mbox /absolute/path/to/gmail.mbox \
  --hotmail-mbox /absolute/path/to/hotmail.mbox \
  --yahoo-json /absolute/path/to/yahoo.json
```

Source options may be repeated. The command refuses `data/jobs.db`, the legacy database paths,
and missing or incorrectly versioned databases by default. `--allow-live-database` only removes
that local guard; it does not grant authorization. A live replay still requires a separate
backup, preflight, evidence review, explicit approval, and post-import validation task.

Before any live replay, run the approval-gate rehearsal. It opens the source database read-only,
creates a SQLite-safe copy in an external output directory, produces a candidate CSV and JSON
evidence, replays the supplied exports twice against only the copy, and verifies source checksum,
historical row preservation, idempotency, integrity, and foreign keys:

```bash
backend/.venv/bin/python scripts/rehearse_historical_interviews.py \
  --source-database data/jobs.db \
  --output-directory /absolute/external/rehearsal-output \
  --gmail-mbox /absolute/path/to/gmail.mbox \
  --hotmail-mbox /absolute/path/to/hotmail.mbox
```

Provider inputs are independent and repeatable; omit providers that are unavailable. Add
`--yahoo-json` only for a raw-message export containing `subject`, `sender`, and `body` strings.
The existing structured Yahoo opportunity/application export is not compatible and must not be
treated as raw email. Use `--cleanup` only when the disposable database and reports no longer need
manual review; otherwise they are preserved outside the repository.

## Gmail and Hotmail OAuth IMAP synchronization

Sprint 11 reuses the bounded, read-only IMAP transport for Gmail and Hotmail. It does not accept
mailbox passwords. Supply either a short-lived OAuth access token or a client ID and refresh token
through the process environment. Never store these values in `.env` files, command history,
fixtures, logs, or the repository.

```bash
export GMAIL_IMAP_USERNAME="your-gmail-address"
export GMAIL_OAUTH_ACCESS_TOKEN="short-lived-access-token"
export GMAIL_IMAP_FOLDER="INBOX"

export HOTMAIL_IMAP_USERNAME="your-hotmail-address"
export HOTMAIL_OAUTH_CLIENT_ID="registered-application-client-id"
export HOTMAIL_OAUTH_REFRESH_TOKEN="refresh-token"
export HOTMAIL_IMAP_FOLDER="Inbox"
```

The accepted variable suffixes are `_IMAP_USERNAME`, `_IMAP_FOLDER`,
`_OAUTH_ACCESS_TOKEN`, `_OAUTH_CLIENT_ID`, `_OAUTH_CLIENT_SECRET`, and
`_OAUTH_REFRESH_TOKEN`, prefixed with `GMAIL` or `HOTMAIL`. A client secret is optional for public
OAuth clients. The application never prints these values.

First list exact folders or perform a bounded dry run. These commands make no database writes and
select mailboxes read-only:

```bash
backend/.venv/bin/python scripts/sync_oauth_imap.py \
  --provider gmail --folder jobs --since-date 2024-07-01 --count-only \
  --output-json /absolute/external/path/gmail-count.json

backend/.venv/bin/python scripts/sync_oauth_imap.py \
  --provider hotmail --folder Job --since-date 2024-07-01 \
  --dry-run --limit 25 \
  --output-json /absolute/external/path/hotmail-dry-run.json
```

The verified production folder names are case-sensitive: Gmail `jobs` and Hotmail `Job`.
Count-only mode performs server-side UID search without fetching headers or bodies. Outlook may
return its final UID once when a range begins above the mailbox maximum; the transport recognizes
only that exact one-UID sentinel as complete and still rejects larger repeated or overlapping
pages.

Temporary synchronization requires an explicit revision-`0006` database. A second identical run
must create zero duplicate evidence:

```bash
export JOBS_DB_PATH="$(mktemp -d)/oauth-sync.db"
backend/.venv/bin/python -m alembic upgrade 20260712_0006
backend/.venv/bin/python scripts/sync_oauth_imap.py \
  --provider gmail --folder jobs --since-date 2024-07-01 \
  --database "$JOBS_DB_PATH" --sync --limit 100
```

Production operation is not authorized merely because the code exists. The offline preflight and
live run require the exact runtime path, an explicitly approved current checksum, current verified
backup metadata, approved provider-specific dry-run JSON, `--allow-live-database`, and the literal
token `GMAIL-LIVE-SYNC` or `HOTMAIL-LIVE-SYNC`. Run those only in a separately approved operation.

## Yahoo IMAP Jobs-folder synchronization

Yahoo IMAP uses an app password only. Never provide the primary Yahoo password. Credentials are
read only from the current process environment and must never be placed in an `.env` file,
database, command-line argument, log, fixture, screenshot, or documentation:

```bash
export YAHOO_IMAP_USERNAME="your-yahoo-address"
export YAHOO_IMAP_APP_PASSWORD="your-yahoo-app-password"
export YAHOO_IMAP_FOLDER="job"
```

Discover the exact server folder name without selecting it for writes:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py --list-folders
```

Run a read-only metadata and classification preview. The mailbox is selected with `readonly=True`,
and every fetch uses `BODY.PEEK`; no flags, moves, deletes, or expunges are issued:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --count-only
```

Count-only performs paginated, date-bounded UID search and fetches no message headers or bodies.
It reports first/last UID, search page count, and whether completeness was proven. After reviewing
that count, use a bounded dry run:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --dry-run --limit 100 \
  --connect-timeout 30 --read-timeout 60 \
  --progress-every 100 --max-mime-parts 50 \
  --max-fallback-message-bytes 10485760 \
  --output-json /absolute/external/path/yahoo-dry-run.json
```

`--output-json` is available only for dry runs, refuses repository paths, and writes the same
sanitized aggregate result printed to stdout. It never includes message content or credentials.

Resume after a completed batch without processing earlier UIDs:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --after-uid LAST_COMPLETED_UID \
  --dry-run --limit 100 --connect-timeout 30 --read-timeout 60
```

`--start-uid N` starts inclusively at `N`; `--after-uid N` starts at `N + 1`. Reports distinguish
complete search total, selected batch, attempted processing, successful completion, accepted
candidates, and failures. Metrics count search, header, BODYSTRUCTURE, and body fetch commands.

Temporary-database integration requires an explicit database already migrated to `0006`:

```bash
JOBS_DB_PATH=/tmp/yahoo-sync.db backend/.venv/bin/python -m alembic upgrade 20260712_0006
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --database /tmp/yahoo-sync.db --sync
```

Sprint 10 keeps `data/jobs.db` protected unless every offline approval gate passes. Validate the
exact live database, revision, checksum, verified pre-migration backup, approved dry-run evidence,
credentials, and TLS configuration without opening a Yahoo connection or writing the database:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 \
  --database /Users/solovatmacpro16/Downloads/job-search-intelligence/data/jobs.db \
  --preflight-live \
  --backup-metadata /Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-10/pre-migration/jobs-20260714T004251Z.metadata.json \
  --dry-run-evidence /absolute/path/to/approved-yahoo-dry-run.json
```

The first live batch is additionally locked to UID `53290`, exactly 100 messages, and the literal
confirmation token `YAHOO-LIVE-SYNC`. A live run is never implied by successful preflight:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --start-uid 53290 --limit 100 \
  --database /Users/solovatmacpro16/Downloads/job-search-intelligence/data/jobs.db \
  --sync --allow-live-database --confirm-live-sync YAHOO-LIVE-SYNC \
  --backup-metadata /Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-10/pre-migration/jobs-20260714T004251Z.metadata.json \
  --dry-run-evidence /absolute/path/to/approved-yahoo-dry-run.json
```

The production report includes pre/post checksums and database health, row deltas, checkpoint,
classification and unresolved counts, UID progress, a run identifier, and an immediate read-only
evidence pass proving every candidate has stable provenance, classification, metadata, and
relationship links. Verification opens SQLite in read-only/query-only mode and never invokes
normal persistence code, creates an import row, or updates timestamps. The checkpoint timestamp is
the only expected audit mutation after verification succeeds.

### Sprint 10.1 incident analysis and recovery

The first production attempt is preserved at checksum
`e82d1fa0e4e751ec14b36cf82298e0931c81631698704c0d1152bae7bfe52bc1`. Do not run the normal sync
command against it. Analyze the incident without credentials, Yahoo access, or database writes:

```bash
backend/.venv/bin/python scripts/analyze_yahoo_incident.py \
  --database /Users/solovatmacpro16/Downloads/job-search-intelligence/data/jobs.db \
  --dry-run-evidence /Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-10/yahoo-dry-run/yahoo-dry-run-53290-100.json \
  --output-json /Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-10-1-idempotency-incident/recovery-analysis.json
```

Rehearse scoped rollback only on a disposable copy; the command refuses the live database:

```bash
backend/.venv/bin/python scripts/analyze_yahoo_incident.py \
  --database /Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-10-1-idempotency-incident/jobs-20260714T060631Z.sqlite3 \
  --recovery-plan \
  --disposable-copy /tmp/yahoo-incident-recovery.sqlite3 \
  --rollback-disposable
```

After that copy has been reduced to the verified 7,718-job/four-import baseline, a future approved
rehearsal can rebuild the entire 100-UID batch against only the disposable database and require
the same read-only verification used for production:

```bash
backend/.venv/bin/python scripts/sync_yahoo_imap.py \
  --folder job --since-date 2024-07-01 --start-uid 53290 --limit 100 \
  --database /tmp/yahoo-incident-recovery.sqlite3 --sync --verify-idempotency
```

Future recovery is locked to UIDs `53314`, `53336`, `53355`, `53375`, and `53386`, the incident
checksum, the verified revision-`0006` incident backup, the approved dry-run evidence, and token
`YAHOO-INCIDENT-RECOVERY`. First run `scripts/recover_yahoo_incident.py` with `--preflight`; use
`--recover-missing` only in a separately approved live-recovery task. Recovery fetches only those
UIDs, records cross-account identifier conflicts without creating jobs, verifies the five rows
read-only, and advances the checkpoint only after all 100 original UIDs are represented.

IMAP identity uses Yahoo provider, normalized account namespace, exact folder,
UIDVALIDITY, and UID; Message-ID remains separate evidence. The client issues the inclusive,
server-side search `UID SEARCH SINCE 01-Jul-2024 UID <checkpoint>:*` before fetching any headers or
bodies. IMAP `SINCE` uses Yahoo's IMAP internal date, which may differ from the sender-provided
`Date` header. Both dates and the requested since-date are retained for audit, and the `Date`
header never overrides the server search result. Checkpoints are isolated by provider, account,
folder, and requested since-date, then advance by UID within that scope. A UIDVALIDITY change stops
before fetch and requires an explicit future rescan decision. Broken pipes trigger a bounded
reconnect, read-only folder reselection, UIDVALIDITY verification, and retry of the same UID.
Partial MIME or processing failures are reported, the checkpoint remains before the first failed
UID, and repeated successful messages are idempotent. There is no background polling.

The connect timeout passed to `IMAP4_SSL` bounds connection establishment, TLS setup, and the
initial server greeting. Immediately after construction, the read timeout is applied directly to
the underlying IMAP SSL socket, so login, folder operations, UID search, every header/body/MIME
fetch, NOOP, logout, and reconnect cannot wait indefinitely. A MIME timeout discards the
connection and retries the same UID once after read-only reselection and UIDVALIDITY verification.
A second timeout creates one failure for that UID; the following UID starts with a fresh
connection. Multipart messages use one BODYSTRUCTURE fetch, bounded local parsing, and one fetch of
the preferred non-attachment text/plain part or an HTML fallback. Attachment bodies are never
fetched. `--max-mime-parts` is a parser-complexity guard and never drives numbered network probing.
Progress uses counters and UIDs only; it never includes message content, identity, or credentials.

BODYSTRUCTURE parsing accepts nested lists, quoted and escaped strings, literals, NIL values,
language and body-location extensions, dispositions, and encoded MIME parameters. If the structure
is malformed or exceeds the local complexity guard, the client makes one bounded full-message
fallback request using `BODY.PEEK[]<0.N>`, where `N` is one byte above
`--max-fallback-message-bytes`. The default is 10 MiB. A response over that limit creates one UID
failure and processing continues. The full message is parsed locally without executing HTML,
loading remote content, or separately fetching attachments. Fallback metrics distinguish parser
failures, attempts, successes, failures, and oversized messages.

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
