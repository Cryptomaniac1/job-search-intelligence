# Email Intelligence

## Implemented sources

- Gmail MBOX
- Hotmail MBOX
- Yahoo structured JSON import

## Deterministic classification

Sprint 5 classifies every newly accepted message into exactly one canonical business event using
normalized subject, sender, and body signals. The engine is provider-agnostic, versioned,
explainable, and independent of LLMs or embeddings.

Canonical types:

- application confirmation;
- recruiter outreach, follow-up, and reply;
- interview invitation, confirmation, reschedule, and cancellation;
- assessment invitation and reminder;
- offer, update, expiration, acceptance, and decline;
- rejection and position closed;
- ghosting, networking, referral, general company communication, and unknown.

Each classification records confidence, classifier version, and reasons. Only application
confirmations create or match legacy job records. Other messages create provenance and
classification evidence. Sprint 7 consumes interview and assessment classifications through the
versioned `deterministic-interview-v1` extractor.

Interview extraction preserves matched signals, parsed values, missing/ambiguous-field reasons,
provider/account, and original timezone text. UTC is stored only when a timezone is explicit.
Interview aggregates require deterministic job linkage; company-only or cross-account inference is
not allowed. Unresolved messages remain immutable event evidence and never create jobs.

Sprint 8 adds an operator-driven historical replay path for the original Gmail/Hotmail MBOX and
Yahoo raw-message JSON exports. The replay scans every source message but accepts only one of the
six deterministic Interview Pipeline classifications. Conflicting event-type signals are ignored.
Existing imported-message and classification rows are reused without mutation; missing evidence
is additive, and the `(source_message_identity, extractor_version)` constraint plus an explicit
pre-insert check makes repeat runs a database no-op.

Historical matching preserves provider/account separation and uses an existing provenance job or
an explicit job/requisition identifier only. Recruiter linkage requires a deterministic recruiting
role signal and compatible company evidence; company match alone never creates a job relationship.
Missing or conflicting timezones remain recorded as ambiguity evidence and are never converted to
fabricated UTC values.

The Sprint 8 rehearsal workflow accepts Gmail MBOX, Hotmail MBOX, and Yahoo raw-message JSON as
independent inputs. It never requires all providers. Before replay approval, it creates a
SQLite-safe disposable copy outside the repository, writes a candidate CSV and JSON evidence,
runs the identical replay twice, and verifies that the source checksum and all pre-existing rows
are unchanged. The disposable database and reports remain available for human review unless the
operator explicitly requests cleanup.

No compatible Yahoo raw-message export is currently available. Yahoo replay remains blocked until
records containing the original subject, sender, and body are provided. Structured Yahoo
opportunity/application data must not be converted into or treated as raw email evidence.

## Planned

- Thread reconstruction
- Attachment parsing
- Optional second-stage AI classifier
- Live provider synchronization
