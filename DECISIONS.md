# Major Decisions

SQLite selected instead of PostgreSQL.

Reason

Simple deployment.

---

FastAPI selected instead of Flask.

Reason

Typing, async support, OpenAPI.

---

Separate email accounts remain independent.

Yahoo

PM / TPM

Hotmail

Marketing

Gmail

Sales Engineer

Reason

Improves search quality and resume targeting.

---

Classification should use multiple signals.

Never rely only on subject line.

Use

- sender
- body
- thread history
- recruiter patterns

---

Recruiter CRM is a first-class feature.

It is not just an email tracker.

---

## Imported-message identity and provenance

Decision: imported messages use a provider-scoped, versioned SHA-256 identity. RFC Message-ID is
preferred; stable normalized message metadata and content provide the fallback.

Reason: Python `hash()` changes across processes and cannot support repeat-safe synchronization.
Provider namespacing preserves the intentional Gmail, Hotmail, and Yahoo separation.

Decision: first acceptance creates one immutable `imported_messages` provenance row. Repeat import
attempts add an `email_imports` summary but do not overwrite or duplicate the message provenance.

Decision: imported historical jobs return HTTP 409 from the existing delete route instead of being
hard-deleted. Historical cleanup requires a separate reviewed migration or archival design.

---

## Deterministic classification evidence

Decision: every newly accepted imported message receives exactly one canonical classification from
the versioned deterministic classifier. Classification uses normalized subject, sender, and body
signals and records confidence plus human-readable reasons.

Reason: Recruiter CRM and interview modeling need stable, reproducible business-event evidence
before they create domain objects. LLMs, embeddings, and probabilistic second-stage classification
are explicitly outside Sprint 5.

Decision: classification evidence is additive and versioned. New classifier versions append
evidence rather than replacing prior classifications. No historical records are automatically
backfilled or modified.

---

## Deterministic interview evidence

Decision: interview extraction is deterministic, provider-agnostic, versioned, and explainable.
Email remains the source of truth; calendar data may augment it later but cannot replace evidence.

Decision: `interviews` stores the current linked aggregate, while `interview_events` preserves each
source message and extraction result. Reschedules and cancellations update the aggregate without
deleting earlier evidence. Missing or ambiguous timezones preserve local text and never fabricate
UTC.

Decision: a job must be identified deterministically before an interview aggregate is created.
Company-only and cross-account matching are prohibited. Unresolved messages remain event evidence.
Sprint 7 performs no historical backfill and uses no LLM, embedding, or external API.

---

## Yahoo IMAP synchronization

Decision: Yahoo synchronization uses IMAP over certificate-verified TLS on port 993 and an app
password supplied only through process environment variables. Plaintext IMAP, primary-password
handling, credential persistence, and credential logging are prohibited.

Decision: mailbox access is read-only and exact-folder scoped. Synchronization first applies an
inclusive server-side IMAP `SINCE` filter using the server internal date, then uses UID and
UIDVALIDITY for identity and incremental progress within that date scope. The sender `Date` header
is audit evidence and never locally excludes a server match. The client fetches headers before
required text parts, records attachment metadata without downloading attachment bodies, and never
issues flag, move, delete, or expunge commands.

Decision: transport identity includes provider, account namespace, folder, UIDVALIDITY, and UID.
Message-ID remains separate evidence. UIDVALIDITY changes stop synchronization and require an
explicit rescan decision. Checkpoints include the requested since-date so changing scope cannot
silently skip messages. A broken connection is discarded and rebuilt before retrying the same UID.
Socket connect and read deadlines apply to every IMAP operation. Multipart layout is obtained with
one BODYSTRUCTURE response and parsed locally under a configurable complexity cap; sequential
numbered MIME probing is prohibited. Count-only mode performs paginated UID-range search without
fetching message data. Full pages advance to the highest UID plus one; repeated or non-monotonic
pages stop with `search_complete: false`. Operational progress contains counters and UIDs only.
Sprint 9 has no background polling and no live-database override.

Decision: malformed or over-complex BODYSTRUCTURE data uses one bounded partial full-message
fallback, defaulting to 10 MiB plus one detection byte. Oversized or locally unparseable fallbacks
create one failure for the UID and do not stop the batch. HTML parsing tolerates nonstandard marked
sections as inert text; it never interprets them as IMAP state or executes their contents.
