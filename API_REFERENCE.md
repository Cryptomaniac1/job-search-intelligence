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
additive counters described above are returned.

### `GET /imports`

Lists import-attempt summaries. Repeat attempts are intentionally visible.

### `GET /email-classifications`

Lists classification evidence with optional `classification`, `provider`, and `limit` filters.
Each record includes stable message identity, optional job ID, canonical type, confidence,
classifier version, explainable reasons, and creation time.

## Recruiters

- `GET /recruiters` — lists recruiter profiles and their companies, email addresses, contact count,
  and explicit job relationships. Optional exact normalized filters: `company` and `email`.
- `GET /recruiters/{id}` — returns one recruiter profile or `404`.

Sprint 6 provides no recruiter write endpoints.

## Analytics

- `GET /analytics/overview`
- `GET /analytics/timeline`
- `GET /analytics/roles`
- `GET /analytics/companies`

These remain status-based prototype analytics; Sprint 1 does not change their contracts.
