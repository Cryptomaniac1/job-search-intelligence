# Domain Model

## Status

The entities below are **target domain concepts** for the planned Career Operating System. They
provide stable business vocabulary for future design and refactoring. They are not a statement that
corresponding ORM models, tables, endpoints, or workflows already exist.

Sprint 0 does not add domain tables. The implemented SQLite schema remains limited to `jobs` and
`email_imports`.

## Target domain concepts

### Application

A person's candidacy for a specific job opportunity. It records lifecycle state, application date,
source, selected resume, and links to relevant communication and outcomes. An Application belongs
to a Job and may progress through Interviews to an Offer or another terminal outcome.

### Email

An immutable message received from or sent to a job-search participant. Email is the system of
record for imported communication and may provide evidence about an Application, Recruiter,
Company, Interview, or Offer. Provider/account provenance must remain intact.

### Recruiter

A person involved in sourcing or managing a candidacy. A Recruiter may work with one or more
Companies over time and communicate through Emails. Relationship history should be derived from
preserved interactions rather than overwriting earlier facts.

### Company

An organization offering Jobs and employing or engaging Recruiters. A Company provides the shared
context for applications, communication history, interviews, and offers.

### Job

A specific employment opportunity or posting, including title, location, description, source,
requisition identity, and other listing metadata. A Job may be discovered before an Application
exists. The current `jobs` table combines aspects of this target concept with application tracking;
separating them is future migration work.

### Interview

A scheduled or completed evaluation stage associated with an Application. It may include stage,
participants, timing, notes, and calendar evidence. Calendar data augments rather than replaces
email evidence.

### Resume

A versioned candidate document used or considered for Applications. It may have job-family,
industry, skill, and effectiveness metadata. Resume version history must be preserved.

### Offer

A formal employment proposal resulting from an Application. It may contain compensation, terms,
dates, negotiation history, and outcome. An Offer is distinct from merely assigning an `offer`
status to a job record.

## Target relationships

- A Company has Jobs and may have Recruiters.
- A Job may have zero or more Applications over career history.
- An Application uses a Resume and may have Emails, Interviews, and an Offer.
- Emails may provide evidence for several related concepts while retaining their original account.
- Interviews belong to Applications and may be augmented by calendar events.

These relationships are design guidance only. Detailed persistence design requires a separately
reviewed migration plan that preserves the current historical data.
