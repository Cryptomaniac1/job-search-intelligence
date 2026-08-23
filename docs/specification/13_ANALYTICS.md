# Analytics

## Sprint 12.2 corrected analytics requirements

Dashboard analytics must be reproducible from stored evidence and must distinguish business
activity from ingestion activity.

### Authoritative attribution sources

- The outbound-submission denominator comes from the user-maintained application plan or an
  equivalent dated LinkedIn Applied submission ledger. Confirmation emails are evidence of
  delivery, not a complete denominator.
- The supplied funnel analysis is the current reviewed baseline for account and role performance:
  4,618 applications, 314 hiring-manager/team opportunities, 16 finals, and zero offers.
- Account defaults are explicit: `solovat@yahoo.com` is Product Management/TPM;
  `solovat@hotmail.com` and `soultanovr@gmail.com` are Marketing; and
  `ibuildanapp@gmail.com` is Operations/Sales Engineering. A role explicitly extracted from a
  message or linked job overrides the account default.
- Synchronized email subjects and bodies may deterministically attribute company and role, but
  every aggregate must disclose synchronized-message coverage, linked-job coverage, unresolved
  company count, and whether a role is explicit or only an account default.
- Calendar summaries may deterministically attribute interview company, role, and stage. Calendar
  rounds are not unique applications and cannot be used directly as application conversions.
- A missing manual-plan month is filled only with deduplicated synchronized application
  confirmations and is explicitly labeled as email-confirmation coverage, never as a zero.

### Definitions

- An application is a job/application record whose effective status is not `new`, `saved`, or
  `withdrawn`. First-class `applications` data takes precedence over the legacy job status.
- Application denominators use canonical identities: first-class application ID, otherwise
  provider/account plus normalized confirmation identity, otherwise the source job ID. Repeated
  historical imports and overlapping representations never count as additional applications.
- Application dates come only from `applications.applied_at` or `jobs.applied_at`. Import,
  first-seen, and last-seen timestamps must never be substituted for an application date.
- Recruiter replies, interviews, offers, and rejections require deterministic evidence linked to
  a job/application. Mailbox assignment and legacy job status alone are not conversion evidence.
- Multiple messages for the same job and stage count as one conversion, while evidence rows remain
  immutable.
- Unlinked evidence is reported as a data-quality total and excluded from conversion rates.
- Import activity is displayed separately from application activity.

### Period comparison

Application velocity shows rolling 30-, 60-, and 90-day totals plus calendar-month activity. Each
window uses the fully covered LinkedIn Applied ledger first, then the manual plan, then
email-confirmation evidence. These sources are never added together for the same day.

For the reviewed attributed dashboard, `combined_unique_applications` is populated for every
month from July 2024 through the snapshot date. The manual application plan is authoritative in a
month where it contains rows, except that a fully covered LinkedIn Applied ledger takes precedence
because it records the direct application action. In other months, the value is the count of
deduplicated synchronized `APPLICATION_CONFIRMATION` evidence, using linked job identity when
available and stable message identity otherwise. Sources are never added together for the same
month. A partial first ledger month is not treated as full-month coverage. Email-only months are
explicitly labeled as a conservative floor until the relevant mailbox checkpoint has reached the
end of the selected folder.

### Sprint 12.3 production analytics release

Sprint 12.3 completes the selected Gmail, Hotmail, and Yahoo folder backlogs before rebuilding the
attributed snapshot. The dashboard monthly table is the primary operational view and shows:

- plan applications, synchronized application confirmations, the selected combined unique
  application denominator, and month-over-month change;
- distinct linked recruiter replies, linked interview email evidence, calendar interview rounds,
  offers, and rejections;
- reply and interview-conversion rates using combined unique applications as the denominator;
- an explicit unlinked-evidence count for outcomes excluded from conversion rates.

Email outcomes are deduplicated by linked job and outcome group. Repeated messages remain immutable
evidence but do not inflate a conversion. Calendar interview rounds stay separate because several
rounds can belong to one application. A missing deterministic job link never becomes a fabricated
conversion.

The release also provides `./start_backend.sh` as the canonical one-command local startup path on
port 8000. It uses the repository Python environment and the ignored runtime database at
`data/jobs.db` unless an explicit database override is supplied.

The snapshot builder requires the operator-supplied application plan, funnel document, and ICS
calendar export. Those local source files are not copied into the repository or runtime database.
If any source is unavailable, the existing snapshot must be labeled stale rather than rebuilt from
partial evidence or invented replacements.

The current local snapshot was regenerated on 2026-08-21 through that date with all three
operator-supplied sources available, the approved Marketing and PM Gmail MBOX archives loaded,
and the verified legacy LinkedIn Applied ledger reconciled. It reports 4,434 combined unique
applications, 313 calendar interview events, and trailing 30 / 60 / 90-day application totals of
230 / 398 / 535. The direct ledger records 189 July 2026 applications and 162 August 2026
applications; its one-entry partial June month remains email-covered. Snapshot generation is
read-only with respect to the runtime database.
Completed months compare with the preceding complete month. The current partial month compares
with the same number of elapsed days in the preceding month, rather than unfairly comparing a
partial month with a complete month. A zero comparison baseline is reported as `No prior
baseline`, not as an infinite or fabricated percentage improvement. Undated applications are
excluded from date windows and disclosed.

### Calendar review

Calendar interview review is additive evidence analysis. It must:

- read an operator-supplied ICS export without importing it into the runtime database;
- use local-time calendar dates and an inclusive requested date range;
- count deterministic interview/screening events, deduplicate identical occurrences, and exclude
  cancelled or clearly non-job events;
- report ambiguous candidates separately;
- emit counts only—never summaries, descriptions, attendees, event IDs, or locations.

### Role and company analytics

- Role and company denominators use the corrected application population.
- A **confirmed resume submission** is a distinct, immutable `APPLICATION_CONFIRMATION` email
  identity. It is evidence of a submission, not a duplicate count of every message or a claim
  that every job row represents a completed application.
- The resume-submission-by-role tables include only deterministic role evidence: a specific role
  found in the email subject/body, or the documented provider-account fallback when no specific
  role is present. Unresolved confirmations are excluded from role totals and shown as a separate
  count; analytics must never invent a role to make the table sum to all confirmations.
- For the `ibuildanapp@gmail.com` archive, the fallback label is `Operations / Sales Engineering`.
  Specific evidence takes priority: Solutions Consulting, Sales Engineering, Delivery Management,
  and Operations Management remain separate categories.
- Outcomes use distinct evidence-linked jobs.
- Company grouping is case-insensitive and preserves the most common display spelling.
- Last activity uses a real application or linked event date, never an import timestamp.
- Company analytics are directional when company extraction is unknown or low quality; data
  cleaning must not silently rewrite historical evidence.

### Target KPIs

- Response rate
- Interview conversion
- Offer conversion
- Time to interview
- Resume effectiveness

Time-to-interview and resume-effectiveness analytics remain later iterations because current live
interview evidence is not linked to applications and first-class resume/application coverage is
not yet representative.
