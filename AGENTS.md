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

