# Recruiter CRM

## Sprint 6 foundation

Sprint 6 introduces first-class recruiter evidence for newly accepted messages classified as
`RECRUITER_OUTREACH`, `RECRUITER_REPLY`, or `RECRUITER_FOLLOW_UP`. It is deterministic,
provider-agnostic, additive, and independent of LLMs.

The extractor records a recruiter only when it has a valid human sender address and deterministic
company evidence. It may retain the observed name, title, signature, LinkedIn profile URL, and
phone number. Company normalization removes legal suffix variations for matching without creating
a full Company entity or replacing the observed company spelling.

Matching is scoped to the normalized company and uses this order:

1. normalized email address;
2. exact recruiter name plus company;
3. exact signature plus company when no sender name is available.

Recruiters are never merged across companies automatically. Existing non-empty profile evidence
is preserved.

Job relationships require an explicit job or requisition identifier that resolves to an existing
job. Company equality alone never creates a job link. Repeated observations update `last_seen_at`,
preserve the first source message, and do not duplicate the relationship.

Implemented relationship types are `primary_recruiter`, `sourcer`, `coordinator`,
`hiring_contact`, and `unknown`. Sprint 6 does not create interview or offer entities, perform a
historical backfill, call LinkedIn, synchronize live mail, or use probabilistic inference.

## Future CRM capabilities

Communication history, response latency, reminders, notes, relationship scores, and editing remain
planned.
