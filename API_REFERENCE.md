# API Reference

This file documents verified current routes. Target APIs remain in the specialized product and API
design documents.

## System and dashboard

- `GET /health` — returns local API health and version.
- `GET /` — serves the dashboard.
- `GET /static/*` — serves dashboard assets.

## Jobs

- `POST /jobs/upsert` — creates or updates a scanner job using the existing payload contract.
- `GET /jobs` — lists jobs with existing competition, status, search, account, role, and limit
  filters.
- `PATCH /jobs/{job_id}/status` — updates status and optionally notes.
- `DELETE /jobs/{job_id}` — retains the existing hard-delete behavior for non-imported scanner
  records. Returns `409` for imported historical records, which cannot be hard-deleted.
- `GET /jobs/export.csv` — exports the existing job CSV representation.

## Imports

### `POST /imports/mbox`

Uploads a Gmail or Hotmail MBOX file using the existing multipart fields `mailbox_name` and `file`.
Existing response fields remain unchanged. Sprint 1 adds:

- `newly_imported`
- `already_imported`
- `matched`
- `unmatched`
- `failed`

Repeated messages increment `already_imported` and do not create another job or provenance row.
Every newly accepted MBOX message receives exactly one deterministic classification. Only
`APPLICATION_CONFIRMATION` retains the existing job match/create workflow; other classifications
create classification evidence without creating interview or job records. The three recruiter
classifications may create deterministic recruiter evidence; a job link requires an explicit job
or requisition identifier.

### `POST /imports/yahoo`

Imports the existing Yahoo JSON `records` payload. Existing fields remain unchanged; the same five
additive counters described above are returned. Optional `subject`, `sender`, and `body` fields let
new Yahoo message evidence use the same deterministic classification, recruiter, and interview
pipeline while legacy structured application records retain their existing identity behavior.

### `GET /imports`

Lists import-attempt summaries. Repeat attempts are intentionally visible.

### `GET /sync/status`

Returns read-only synchronization state for Gmail, Hotmail, and Yahoo: evidence counts,
classification and interview-event counts, checkpoint counts, and checkpoint progress. Account
namespaces are exposed only as short SHA-256 references. The response contains no credentials,
message content, or write controls.

### `GET /email-classifications`

Lists classification evidence with optional `classification`, `provider`, and `limit` filters.
Each record includes stable message identity, optional job ID, canonical type, confidence,
classifier version, explainable reasons, and creation time.

## Recruiters

- `GET /recruiters` — lists recruiter profiles and their companies, email addresses, contact count,
  and explicit job relationships. Optional exact normalized filters: `company` and `email`.
- `GET /recruiters/{id}` — returns one recruiter profile or `404`.

Sprint 6 introduced no recruiter write endpoints. Sprint 12 adds only the conservative relationship
state endpoint documented below; recruiter identity evidence remains importer-owned.

## Interviews

- `GET /interviews` — lists linked interview aggregates. Optional filters: `status`,
  `interview_type`, `job_id`, `recruiter_id`, `from_date`, `to_date`, `provider`, exact `company`,
  and `upcoming=true`.
- `GET /interviews/upcoming` — lists future, non-cancelled interviews.
- `GET /interviews/{id}` — returns the aggregate, linked job/recruiter summaries, schedule, and
  chronological immutable event evidence, including classifier/extractor references.

Sprint 7 provides no interview write endpoints. Messages without deterministic job linkage remain
as unresolved event evidence and do not appear as fabricated interview aggregates.

Sprint 8 adds no write endpoint. Historical Gmail/Hotmail/Yahoo replay is an approval-gated local
operator command; the existing read-only interview API and payload contracts are unchanged.

## Analytics

- `GET /analytics/overview`
- `GET /analytics/timeline`
- `GET /analytics/roles`
- `GET /analytics/companies`

These remain status-based prototype analytics; Sprint 1 does not change their contracts.

## Version 1 product APIs

Revision `20260808_0007` adds the following local product APIs without changing the existing job,
import, recruiter, interview, or synchronization contracts:

- `GET /applications` and `POST /applications` — list or create application records linked to an
  existing job. One application per job is enforced.
- `GET /applications/{id}` and `PATCH /applications/{id}` — inspect or correct application stage,
  source, resume, company, and notes without rewriting source email evidence.
- `GET /companies` and `POST /companies` — list or create normalized company records.
- `GET /companies/{id}/timeline` — return chronological application, email, recruiter, interview,
  offer, and manual-interaction evidence for a company.
- `GET /resumes` and `POST /resumes` — manage versioned resume metadata and safe text content.
- `GET /job-descriptions` and `POST /job-descriptions` — store safe source text and parsed
  requirements/keywords; executable content is not accepted.
- `GET /offers`, `POST /offers`, and `PATCH /offers/{id}` — manage first-class offer records.
- `GET /notes` and `POST /notes` — manage notes attached to supported domain entities.
- `POST /interactions` — record manual, non-email interaction history.
- `PUT /recruiters/{id}/relationship` — update relationship state, reminder, response-latency,
  and last-contact metadata while preserving recruiter identity evidence.
- `GET /analytics/version1` — return application pipeline, source, offer, and resume-effectiveness
  summaries.
- `GET /settings/status` — return database readiness and credential-safe provider status.

All Version 1 write routes require migration `20260808_0007`. Existing databases are never
automatically migrated at startup.
