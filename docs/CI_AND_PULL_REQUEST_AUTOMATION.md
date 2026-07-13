# CI and Pull-Request Automation

## Repository workflow

`.github/workflows/ci.yml` runs for every pull request targeting `main`. All database paths point
to GitHub runner temporary storage. CI must never read, copy, migrate, or create `data/jobs.db`,
`backend/jobs.db.migrated`, or external backup files.

The required jobs are:

- `Repository safety`
- `Python quality`
- `Test suite`
- `Migration safety`
- `Temporary database smoke`

The migration job upgrades, downgrades, and re-upgrades only a runner-temporary SQLite database.
The smoke job uses Pytest temporary directories and checks health, API, dashboard, startup, and
database-path behavior.

## Main-branch ruleset setup — manual action required

Do this only after the CI workflow has run successfully at least once, so GitHub can offer its job
names as required checks.

1. Open the repository on GitHub.
2. Select **Settings → Rules → Rulesets**.
3. Select **New ruleset → New branch ruleset**.
4. Name it `Protect main`, set **Enforcement status** to **Active**, and target the default branch
   or add the exact branch `main`.
5. Leave the bypass list empty unless an emergency process is separately approved.
6. Enable **Restrict deletions**.
7. Enable **Block force pushes**.
8. Enable **Require a pull request before merging**.
9. Require at least one approval, enable **Dismiss stale pull request approvals when new commits
   are pushed**, and enable **Require review from Code Owners**.
10. Enable **Require conversation resolution before merging**.
11. Enable **Require status checks to pass** and select:
    - `Repository safety`
    - `Python quality`
    - `Test suite`
    - `Migration safety`
    - `Temporary database smoke`
12. Enable **Require branches to be up to date before merging** (strict checks).
13. Review the rule summary and select **Create**. Do not enable force pushes or branch deletion.

GitHub does not count a pull-request author's own approval. If Rafael authors a sensitive PR,
requiring his CODEOWNER approval needs another workflow: a second authorized maintainer must own
or approve the affected path, or the PR must remain blocked until an independently approved policy
change is made. Do not silently weaken the rule to work around a solo-review limitation.

## Codex automatic review — manual action required

OpenAI supports automatic GitHub pull-request reviews through the Codex GitHub integration. This
is not configured by a GitHub Actions workflow and availability depends on the connected account,
workspace policy, and repository authorization.

1. Open Codex code-review settings at `https://chatgpt.com/codex/settings/code-review`.
2. Confirm the GitHub account containing `Cryptomaniac1/job-search-intelligence` is connected.
3. Confirm the Codex GitHub application is authorized for this repository.
4. Find `job-search-intelligence` and enable **Automatic reviews** for the repository. Choose the
   repository/team option that reviews all pull requests, not only personal PRs, when available.
5. Open a harmless test pull request and confirm Codex posts a review when the PR changes from
   draft to ready.
6. If automatic review is unavailable, request it manually with `@codex review` and treat the PR as
   blocked until the review completes.

Do not add Codex as a required status check until the installed integration has published a stable
check name in this repository. A Codex review may appear as a GitHub review rather than a check run;
required review policy must be verified from actual repository behavior.

## Auto-merge policy — manual and restricted

Repository-wide automatic merging remains disabled by default. To make GitHub's per-PR
**Enable auto-merge** button available, an administrator may manually select **Settings → General
→ Pull Requests → Allow auto-merge**. This does not merge PRs automatically by itself.

Enable auto-merge on an individual PR only when every changed file is either:

- documentation (`*.md` or files below `docs/`); or
- tests and test fixtures below `tests/`.

Never enable auto-merge for application code, migrations, database paths, workflows, import or
classification logic, historical-data tooling, live synchronization, authentication, credentials,
secrets, or architectural refactors. Eligible PRs still require all status checks, an up-to-date
branch, completed Codex review with no unresolved findings, and all required human/code-owner
approvals.

GitHub's native auto-merge setting does not enforce this file policy by itself. Until a separately
reviewed policy bot is approved, eligibility must be checked manually from the PR's **Files
changed** tab before selecting **Enable auto-merge**.

## Sensitive-change approval policy

Rafael's manual approval is required for:

- Alembic migrations;
- database-path resolution or runtime storage;
- import identity, matching, classification, or recruiter extraction;
- historical-data operations and live migrations;
- live email synchronization;
- authentication, credentials, tokens, or secrets;
- large architectural refactors; and
- CI workflow or branch-governance changes.

`CODEOWNERS` requests Rafael automatically for known sensitive paths. The pull-request template is
the fallback for cross-cutting sensitive changes that cannot be reliably identified by path.
