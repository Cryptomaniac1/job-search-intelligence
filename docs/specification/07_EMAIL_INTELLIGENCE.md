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

## Planned

- Thread reconstruction
- Attachment parsing
- Optional second-stage AI classifier
- Live provider synchronization
