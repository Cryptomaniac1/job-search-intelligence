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
