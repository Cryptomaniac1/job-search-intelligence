# Current Status

This document separates verified implementation from prototypes and target architecture. Items in
the product and architecture documents remain part of the roadmap unless explicitly retired.

## Implemented

- FastAPI application served locally by Uvicorn.
- SQLite persistence with the current `jobs` and `email_imports` tables.
- Local REST endpoints for job upsert, listing, status updates, deletion, CSV export, imports, and
  analytics.
- Gmail and Hotmail MBOX file import for application-confirmation detection.
- Yahoo application-record JSON import.
- Deterministic account-to-role-family mapping for Yahoo, Hotmail, and Gmail.
- Local dashboard and static assets.
- Sprint 0 package skeleton, isolated Pytest suite, linting, formatting, typing, and Alembic
  baseline infrastructure.
- Stable provider-scoped imported-message identity with deterministic SHA-256 fallback.
- Immutable imported-message provenance and repeat-import counters.
- Preservation-first email-to-job merge behavior and imported-record deletion protection.
- Read-only live-database preflight, SQLite-safe backup, copy-only migration rehearsal, rollback
  verification, and duplicate-candidate reporting.
- Historical database at Alembic revision `20260823_0008`, externalized to ignored runtime path
  `data/jobs.db` with canonical environment-variable overrides. The additive reviewed-link and
  company-alias tables are present and initially empty.
- Deterministic, versioned, provider-agnostic email classification engine with explainable reasons
  and additive classification evidence persistence.
- Approval-gated Sprint 5.5 live migration completed with preserved historical row counts and
  logical digests; `email_classifications` began with zero rows.
- Deterministic Recruiter CRM foundation with additive recruiter, company, email, and explicit job
  relationship models, read-only APIs, and dashboard visibility.
- Approval-gated Sprint 6.5 live migration completed with preserved historical counts and logical
  digests; all four Recruiter CRM tables began with zero rows.
- Pull-request CI definition, runtime-database guard, PR template, sensitive-path CODEOWNERS, and
  documented manual setup for main-branch protection, Codex review, and restricted auto-merge.
- Deterministic Interview Pipeline foundation with versioned extraction evidence, additive
  interview aggregates and immutable events, read-only APIs, dashboard visibility, sanitized
  fixtures, and a temporary-database demonstration workflow.
- Approval-gated Sprint 7.5 live migration completed with preserved historical row counts and
  logical digests; `interviews` and `interview_events` were created with zero rows.
- Deterministic historical Interview Pipeline replay for Gmail/Hotmail MBOX and Yahoo JSON
  exports, with provider-scoped idempotency, provenance preservation, explicit-only job matching,
  conservative recruiter linkage, and protected temporary-database tooling.
- Copy-only historical Interview Pipeline rehearsal with independent provider inputs, pre-import
  candidate CSV, machine-readable safety evidence, two-pass idempotency checks, and source
  checksum protection. Gmail and Hotmail raw exports are supported independently; Yahoo replay is
  blocked until a compatible raw-message export is available.
- TLS-only Yahoo IMAP Jobs-folder synchronization transport with exact read-only folder selection,
  inclusive server-side date-bounded UID search, headers-first retrieval, attachment-metadata-only
  MIME handling, count-only search, account/folder/UID identity, date-scoped checkpoint safety,
  explicit socket deadlines, bounded reconnect and same-UID retry, single-response BODYSTRUCTURE
  parsing, monotonic UID-range pagination, fetch-efficiency metrics, unambiguous batch reporting,
  tolerant HTML normalization, bounded full-message fallback for malformed BODYSTRUCTURE data,
  and temporary-database integration. An offline, approval-gated production path validates the
  exact database checksum, backup and dry-run evidence, credentials, TLS configuration,
  confirmation token, and fixed first-batch scope. Approved incident recovery represented 97
  messages, recorded three explicitly accepted unavailable UIDs, and wrote checkpoint UID 53392.
- Shared Gmail and Hotmail OAuth IMAP foundation using XOAUTH2, provider-specific TLS endpoints,
  inclusive server-side date bounds, the existing bounded read-only transport, provider/account/
  folder/date checkpoint isolation, external sanitized evidence reports, and offline production
  approval gates. Temporary two-pass simulations prove repeat-safe evidence ingestion. Real OAuth
  authorization and read-only validation completed for the exact Gmail `jobs` and Hotmail `Job`
  folders: Gmail reported 242 matches and Hotmail reported 6,045 matches since 2024-07-01; bounded
  25-message dry runs completed without failures, database writes, or mailbox mutations. Approved
  bounded production runs represented 100 Gmail and 100 Hotmail messages. Immediate repeat passes
  added zero messages for both providers and mailbox mutations remained zero.
- Read-only `GET /sync/status` API and dashboard panel for provider evidence and checkpoint state.
  Account namespaces are hashed and credentials and message content are never returned.
- Version 1 deterministic classification benchmark with 33 version-controlled sanitized cases,
  including
  withdrawal and explicit recruiter-screen, technical, hiring-manager, panel, onsite, final-round,
  and assessment differentiation. The current fixture benchmark scores 100%; production accuracy
  remains unproven until reviewed real evidence is available.
- Sprint 12 Version 1 candidate with additive Company, Resume, Application, JobDescription, Offer,
  RecruiterRelationship, Note, and Interaction persistence; local correction APIs; company
  timelines; application/offer/resume/company/settings dashboard views; deterministic resume/job
  scoring; and repeatable temporary-database product and performance tests. The candidate
  migration is `20260808_0007` and is applied to the runtime database. Sprint 13's additive
  `20260823_0008` reviewed-link and company-alias migration was rehearsed, backed up, applied,
  and validated without historical row changes.
- Sprint 12 production closeout completed with 7,750 jobs, 10 import audit rows, 297 immutable
  imported messages/classifications/IMAP metadata rows, two recruiters, 12 interview events, and
  three provider checkpoints. Runtime integrity and foreign-key checks pass. The final verified
  external backup is
  `/Users/solovatmacpro16/Documents/job-intelligence-backups/sprint-12/final/jobs-20260808T214439Z.sqlite3`.
- Sprint 12.2 corrected dashboard analytics to use explicit application dates, separate import
  activity, collapse repeated/overlapping rows into canonical application identities, deduplicate
  linked outcome evidence, expose unlinked evidence as data quality, show rolling 30/60/90-day
  application velocity and fair calendar-month changes, and prevent mailbox assignment or legacy
  status from fabricating role/company conversions. A local,
  content-free ICS review found 306 deterministic calendar interview events from 2024-07-01
  through 2026-08-08; it did not import or modify runtime data.
- Sprint 12.2 attribution correction supersedes confirmation-derived activity totals. The local
  ignored snapshot now reproduces the supplied 4,618-application funnel, the plan's 3,155 recorded
  submissions and 20.5 active-day average, 306 calendar interview rounds, explicit account/role
  mapping, and bounded 297-message email-evidence coverage. Months after the plan ends now use
  deduplicated synchronized application-confirmation evidence as a conservative application floor.
- Sprint 12.2 now populates a monthly `combined_unique_applications` series from July 2024 through
  the snapshot date. Recorded plan months remain authoritative; otherwise the series uses
  deduplicated synchronized application confirmations. Email-only months are conservative floors
  while provider checkpoints remain incomplete.
- Sprint 12.3 completed the selected Gmail and Hotmail backlogs and the approved 1,000-message
  Yahoo continuation batch (UIDs through `54425`). The Yahoo repeat verification made zero
  writes and found no logical-state changes. The live database now contains 8,228 jobs, 7,384
  imported messages/classifications/IMAP metadata rows, 48 recruiters, and 178 interview-event
  rows; integrity and foreign-key checks pass at revision `20260808_0007`. The attributed
  dashboard snapshot was rebuilt on 2026-08-21 from the operator-supplied spreadsheet, funnel
  document, and ICS calendar export. The separately approved Gmail MBOX imports added 535
  Marketing messages from `soultanovr@gmail.com` and 17,301 PM messages from
  `solovat@gmail.com`, with account-scoped identities and zero failures. The rebuilt snapshot
  reports 4,247 combined unique applications through the snapshot date, 313 calendar interview
  events, and 82 / 211 / 348 combined unique applications in the trailing 30 / 60 / 90 days.
  A subsequent reconciliation imported 352 verified legacy LinkedIn Applied records from the
  local extension ledger without replacing email or calendar evidence. The corrected snapshot
  reports 4,434 combined unique applications, direct LinkedIn submissions of 189 in July 2026
  and 162 in August 2026, and trailing 30 / 60 / 90-day totals of 230 / 398 / 535. The ledger's
  partial June start remains email-covered rather than being misrepresented as a full month.
  `./start_backend.sh` is the canonical one-command local startup path. Sprint closure verification
  subsequently passed all 227 tests, Ruff, Black, MyPy, Python and JavaScript syntax checks, and
  Git whitespace validation. The Yahoo incident regression fixture now constructs the documented
  historical partial state directly rather than disabling the currently safe importer.
- On 2026-08-22, the approved `ibuildanapp@gmail.com` Gmail Takeout import added 1,662 immutable
  messages with zero import failures. It produced 438 application confirmations, 250 new jobs,
  18 deterministic recruiters, 16 interviews, and 59 interview events without changing existing
  rows. The live database passed integrity and foreign-key checks before and after the import;
  the verified pre-import backup and result record are stored outside the repository. The
  dashboard snapshot now exposes 374 role-attributed confirmed resume submissions: Sales
  Engineering 129, Operations / Sales Engineering 104 (documented account fallback), Operations
  Management 11, Delivery Management 10, and Solutions Consulting 5. The remaining 1,403
  confirmations have no deterministic role and are disclosed rather than assigned speculatively.

## Prototype

- LinkedIn browser extension for scanning visible job cards and saving them to the local API.
- Regex-based application-confirmation classification and metadata extraction.
- Heuristic matching between imported confirmations and job records.
- Company-name extraction quality remains prototype-grade; unknown and ATS-like company values
  limit the current ranking's decision value even though its counts are internally consistent.

Prototype status means that behavior exists but does not yet satisfy the complete product
requirements or production reliability goals.

## In Progress

- Regression coverage expansion for import parsing, matching, and analytics.
- Progressive extraction of the backend monolith into the `backend/app` package.
- Review and separately plan remediation for pre-Sprint-1 duplicate historical records.
- Retain `backend/jobs.db.migrated` temporarily as a local rollback artifact, then remove it only
  through a separately approved maintenance task.

## Planned

- Continued bounded incremental synchronization beyond the first reviewed production batches.
- First-class email-thread reconstruction and richer interview editing/calendar augmentation.
- Resume intelligence and automatic recommendations.
- Follow-up reminders and calendar augmentation.
- AI interview coach and company intelligence.
- Broader browser-extension and LinkedIn workflow support.

The broader architecture in `PRODUCT_REQUIREMENTS.md` and the specialized specifications is the
target architecture, not a claim that every component is currently implemented.
