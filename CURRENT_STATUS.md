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

## Prototype

- LinkedIn browser extension for scanning visible job cards and saving them to the local API.
- Regex-based application-confirmation classification and metadata extraction.
- Heuristic matching between imported confirmations and job records.
- Status-based recruiter, interview, role, company, and timeline analytics.

Prototype status means that behavior exists but does not yet satisfy the complete product
requirements or production reliability goals.

## In Progress

- Regression coverage expansion for import parsing, matching, and analytics.
- Progressive extraction of the backend monolith into the `backend/app` package.
- Domain-model and migration design beyond the current two-table baseline.
- Review and separately plan remediation for pre-Sprint-1 duplicate historical records.

## Planned

- Live Gmail API synchronization.
- Live Yahoo, Hotmail, and other IMAP synchronization.
- Full email classification, including recruiter outreach and interview stages.
- Recruiter CRM and recruiter relationship scoring.
- First-class applications, emails, companies, interviews, resumes, and offers.
- Resume intelligence and automatic recommendations.
- Follow-up reminders and calendar augmentation.
- AI interview coach and company intelligence.
- Broader browser-extension and LinkedIn workflow support.

The broader architecture in `PRODUCT_REQUIREMENTS.md` and the specialized specifications is the
target architecture, not a claim that every component is currently implemented.
