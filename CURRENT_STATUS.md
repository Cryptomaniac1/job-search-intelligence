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
- Historical database at Alembic revision `20260712_0006`, externalized to ignored runtime path
  `data/jobs.db` with canonical environment-variable overrides.
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
  confirmation token, and fixed first-batch scope. No Yahoo synchronization has completed with a
  checkpoint.
- Shared Gmail and Hotmail OAuth IMAP foundation using XOAUTH2, provider-specific TLS endpoints,
  inclusive server-side date bounds, the existing bounded read-only transport, provider/account/
  folder/date checkpoint isolation, external sanitized evidence reports, and offline production
  approval gates. Temporary two-pass simulations prove repeat-safe evidence ingestion. Real OAuth
  authorization and read-only validation completed for the exact Gmail `jobs` and Hotmail `Job`
  folders: Gmail reported 242 matches and Hotmail reported 6,045 matches since 2024-07-01; bounded
  25-message dry runs completed without failures, database writes, or mailbox mutations. Neither
  provider has been synchronized into the runtime database.
- Read-only `GET /sync/status` API and dashboard panel for provider evidence and checkpoint state.
  Account namespaces are hashed and credentials and message content are never returned.
- Version 1 deterministic classification benchmark with 33 version-controlled sanitized cases,
  including
  withdrawal and explicit recruiter-screen, technical, hiring-manager, panel, onsite, final-round,
  and assessment differentiation. The current fixture benchmark scores 100%; production accuracy
  remains unproven until reviewed real evidence is available.

## Prototype

- LinkedIn browser extension for scanning visible job cards and saving them to the local API.
- Regex-based application-confirmation classification and metadata extraction.
- Heuristic matching between imported confirmations and job records.
- Status-based recruiter, interview, role, company, and timeline analytics.

Prototype status means that behavior exists but does not yet satisfy the complete product
requirements or production reliability goals.

## In Progress

- Sprint 11 production email completion. Provider-neutral Gmail/Hotmail OAuth transport,
  checkpointing, live approval gates, status visibility, and benchmark coverage are implemented;
  live synchronization and Yahoo incident recovery remain separately approval-gated operational
  work.
- Sprint 10.1 remediation of the first bounded Yahoo production attempt. The fetch completed 100
  messages, but the original mutating idempotency pass accepted one dependency-ordered message
  and aborted before checkpoint creation. The preserved incident database contains 95 Yahoo
  provenance rows, 19 additive Yahoo jobs, and no checkpoint; the original 7,718 jobs and four
  import rows remain logically unchanged. Live recovery is not yet approved.

- Regression coverage expansion for import parsing, matching, and analytics.
- Progressive extraction of the backend monolith into the `backend/app` package.
- Review and separately plan remediation for pre-Sprint-1 duplicate historical records.
- Retain `backend/jobs.db.migrated` temporarily as a local rollback artifact, then remove it only
  through a separately approved maintenance task.

## Planned

- Approved production Gmail and Hotmail synchronization.
- Approval-gated recovery of the five unresolved Yahoo UIDs, then incremental Yahoo IMAP
  production synchronization; live Hotmail and other IMAP synchronization.
- Recruiter relationship scoring, notes, reminders, and editing.
- First-class applications, emails, companies, resumes, and offers, plus richer interview editing
  and calendar augmentation.
- Resume intelligence and automatic recommendations.
- Follow-up reminders and calendar augmentation.
- AI interview coach and company intelligence.
- Broader browser-extension and LinkedIn workflow support.

The broader architecture in `PRODUCT_REQUIREMENTS.md` and the specialized specifications is the
target architecture, not a claim that every component is currently implemented.
