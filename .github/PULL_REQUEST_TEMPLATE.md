## Summary

<!-- What changed and why? -->

## Files changed

<!-- List the important files or modules changed. -->

## Safety and data impact

- Schema impact: <!-- None, additive migration, destructive migration, or other -->
- Historical-data impact: <!-- None, read-only, additive, or mutation requiring approval -->
- Migration status: <!-- Not applicable, temporary DB verified, or live migration separately approved -->
- Live-database status: <!-- Confirm data/jobs.db was not accessed or modified -->
- Rollback plan: <!-- Exact rollback/recovery approach, or why none is needed -->

## Verification

<!-- Include exact commands and results. -->

- [ ] Pytest passed
- [ ] Ruff passed
- [ ] Black check passed
- [ ] MyPy passed using the configured scope
- [ ] Python syntax checks passed
- [ ] Migration tests passed when applicable
- [ ] JavaScript syntax checks passed when applicable
- [ ] Temporary-database smoke tests passed

## Required review gates

- [ ] This PR does not include `data/jobs.db`, `backend/jobs.db.migrated`, or backup databases
- [ ] No credentials, tokens, secrets, or `.env` files are included
- [ ] Runtime databases and historical records were not modified without explicit approval
- [ ] Sensitive changes are identified below and Rafael has been requested as reviewer

Sensitive areas changed:

- [ ] None
- [ ] Alembic migrations or database paths
- [ ] Import identity, matching, or classification logic
- [ ] Historical-data operations
- [ ] Live synchronization
- [ ] Authentication, credentials, or secrets
- [ ] Large architectural refactor

## Auto-merge eligibility

- [ ] Documentation-only PR
- [ ] Test-only PR
- [ ] Not eligible for auto-merge

Auto-merge must not be enabled until required checks pass, Codex review has no unresolved findings,
and all required human/code-owner approvals are complete.
