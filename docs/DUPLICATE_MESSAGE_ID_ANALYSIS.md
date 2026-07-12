# Existing Duplicate Message-ID Analysis

## Scope and safety

This is a read-only analysis of `backend/jobs.db` before Sprint 1. No historical row was deleted,
merged, updated, stamped, or migrated.

## Findings

- 1,150 duplicated nonblank `confirmation_message_id` groups exist.
- Every group contains exactly two rows: 2,300 rows total and 1,150 excess rows by identity.
- All duplicate pairs stay within one email account; no group crosses account boundaries.
- Every pair has different `linkedin_job_id` values.
- 1,014 groups have identical company, title, and applied-date values.
- 136 groups differ in at least one of company, title, or applied date.

Rows in duplicated groups by source/account:

- Hotmail `email`: 1,442 rows.
- Yahoo `email`: 386 rows.
- Yahoo `yahoo_db`: 386 rows.
- Gmail `email`: 86 rows.

## Likely causes

The exact two-row shape and the 386 Yahoo-email/386 Yahoo-database symmetry strongly suggest
repeat imports and overlapping Yahoo import paths. The legacy importer did not enforce Message-ID
uniqueness and generated unmatched job IDs with process-randomized Python `hash()`. The 1,014
identical-core groups are consistent with duplicate processing of the same confirmation. The 136
differing groups require manual evidence review before any merge decision.

These are evidence-based hypotheses, not automatic cleanup criteria.

## Safe remediation options

1. Leave historical rows unchanged and prevent new duplicates, which is Sprint 1's approach.
2. Build a read-only candidate report comparing every pair's fields and downstream references.
3. Introduce an explicit supersession/archive relationship before hiding duplicate candidates.
4. Merge only through a separately approved, reversible migration with backups and an audit log.

Do not use Message-ID alone to delete existing rows. Different job IDs and the 136 differing-core
groups mean blind cleanup could destroy distinct historical facts.

## Sprint 2 field-level report

The migration-readiness command now produces one CSV row per duplicate Message-ID group with both
job IDs, account, source, company, title, applied date, status, ATS, requisition ID, confidence,
differing fields, and a recommended review category:

```bash
python -m backend.app.database.migration_readiness duplicate-report \
  backend/jobs.db /absolute/external/duplicate-message-id-candidates.csv
```

The Sprint 2 read-only run produced all 1,150 groups outside the repository. Classification counts
were:

- likely exact duplicate: 950;
- probable duplicate needing review: 0;
- conflicting record requiring manual review: 200.

Categories are triage recommendations only. They do not authorize automated cleanup.
