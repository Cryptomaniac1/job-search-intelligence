# Codex: Start Here

This file is the navigation entry point for Codex. It does not replace the existing project documentation.

## Required reading order

1. `AGENTS.md` — operating rules and repository instructions
2. `PROJECT_CONTEXT.md` — purpose and business context
3. `CURRENT_STATUS.md` — implemented, in progress, blocked
4. `MASTER_CONTEXT.md` — documentation index and priority rules
5. Read only the task-relevant detailed documents listed below

## Task-specific documents

| Task | Read |
|---|---|
| Product behavior | `PRODUCT_REQUIREMENTS.md` |
| Architecture | `ARCHITECTURE.md` and `job-search-intelligence-specification/02_SYSTEM_ARCHITECTURE.md` |
| Database | `DATABASE.md` and `SQLITE_SCHEMA.md` |
| API | `API_REFERENCE.md` and `API_SPECIFICATION.md` |
| Email sync/classification | `job-search-intelligence-specification/07_EMAIL_INTELLIGENCE.md` |
| Recruiter CRM | `job-search-intelligence-specification/06_RECRUITER_CRM.md` |
| Browser extension | `job-search-intelligence-specification/08_BROWSER_EXTENSION.md` |
| LinkedIn workflow | `job-search-intelligence-specification/09_LINKEDIN_AUTOMATION.md` |
| Analytics | `job-search-intelligence-specification/13_ANALYTICS.md` |
| Historical decisions | `DECISIONS.md` and `CHAT_HISTORY_DECISIONS.md` |
| Active priorities | `CURRENT_STATUS.md`, then `TODO.md` |
| Future concepts | `careeros-additional-docs/FUTURE_ARCHITECTURE.md` |

## Authority order when documents conflict

1. Explicit instructions in the current Codex task
2. `AGENTS.md`
3. `CURRENT_STATUS.md`
4. `MASTER_CONTEXT.md`
5. `PRODUCT_REQUIREMENTS.md`
6. Specialized design documents
7. `ROADMAP.md` and `TODO.md`
8. Historical or future-looking documents

Do not treat `FUTURE_ARCHITECTURE.md` as approved current architecture.

## Repository rules

- Do not create duplicate canonical documents.
- Update the existing relevant document instead.
- Do not rename or reorganize the repository unless explicitly requested.
- Inspect the code before assuming the documentation is fully current.
- Report documentation/code conflicts before implementing destructive changes.

Stop reading when enough context is gathered.
