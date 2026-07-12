# AGENTS.md

## Purpose

This document provides persistent guidance for coding agents (Codex, ChatGPT Work, etc.) working on the Job Search Intelligence repository.

# Project Mission

Build an AI-powered Career Operating System that automates job search tracking, recruiter relationship management, interview analytics, and career intelligence.

## Core Principles

- Do not break existing email synchronization.
- Preserve historical data; never overwrite records automatically.
- Prefer deterministic logic over LLMs when rules are sufficient.
- Every feature should be testable.
- Keep the application runnable locally.

## Technology Stack

- Python 3.12
- FastAPI
- SQLite (current)
- SQLAlchemy
- Pydantic
- Pytest

## Repository Conventions

app/
    api/
    services/
    models/
    schemas/
    db/
    utils/

tests/
docs/

## Coding Standards

- Type hints required.
- Functions should generally remain under 50 lines.
- Separate business logic from API routes.
- Avoid duplicate code.
- Add unit tests for new features.

## Business Rules

Email account mapping must remain:

Yahoo:
- Product Manager
- Technical Program Manager

Hotmail:
- Marketing

Gmail:
- Sales Engineer
- Delivery Manager

Do not merge these sources unless explicitly requested.

## Data Rules

Emails are the system of record.
Calendar augments email.
Never delete imported records.

## Feature Priorities

1. Email synchronization
2. Classification
3. Recruiter CRM
4. Dashboard
5. Browser extension
6. LinkedIn automation
7. AI recommendations

## Definition of Done

- Code compiles
- Tests pass
- Documentation updated
- No regressions

# Append this section to the existing AGENTS.md

## Documentation Navigation

Before making changes, read `CODEX_START_HERE.md`.

Use the existing documentation as the source of truth. Do not create duplicate versions of product requirements, architecture, database, API, roadmap, or decision documents.

Read only the documents relevant to the current task. Validate documentation against the current code before making structural changes.

When documents conflict, follow this order:

1. Current task instructions
2. `AGENTS.md`
3. `CURRENT_STATUS.md`
4. `MASTER_CONTEXT.md`
5. `PRODUCT_REQUIREMENTS.md`
6. Specialized design documents
7. `ROADMAP.md` and `TODO.md`
8. Historical and future-looking documents

`FUTURE_ARCHITECTURE.md` is exploratory and is not approved for implementation unless the task explicitly says otherwise.

## Git and Sprint Completion Rules

For every sprint:

1. Inspect repository state before making changes:
   - `git status`
   - `git branch --show-current`
   - `git remote -v`

2. Do not overwrite, discard, reset, stash, or commit pre-existing unrelated changes without explicit approval.

3. Never commit:
   - `backend/jobs.db`
   - virtual environments
   - `.env` files
   - credentials, tokens, or secrets
   - cache directories
   - temporary exports or test databases
   - operating-system metadata

4. Before staging:
   - review `git diff`
   - review untracked files
   - confirm `.gitignore` protects local and sensitive files

5. Stage only files belonging to the current sprint unless explicitly instructed otherwise.

6. Before committing:
   - run all sprint verification commands
   - run `git diff --staged`
   - summarize exactly what will be committed
   - confirm the historical database checksum is unchanged when database protection applies

7. Do not create a commit or push to GitHub unless the task explicitly authorizes it.

8. When commit authorization is provided:
   - create one focused commit for the sprint
   - use the requested commit message
   - do not amend unrelated commits
   - do not force-push

9. When push authorization is provided:
   - verify the remote and branch
   - push the current branch to `origin`
   - confirm the local branch is synchronized with GitHub

10. At sprint completion, report:
    - branch name
    - files changed
    - verification results
    - commit hash, if committed
    - push result, if pushed
    - remaining uncommitted or unrelated changes