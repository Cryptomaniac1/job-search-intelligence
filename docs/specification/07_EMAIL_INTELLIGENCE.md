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
classification evidence for future Recruiter CRM and Interview Pipeline sprints.

## Planned

- Thread reconstruction
- Attachment parsing
- Optional second-stage AI classifier
- Live provider synchronization
