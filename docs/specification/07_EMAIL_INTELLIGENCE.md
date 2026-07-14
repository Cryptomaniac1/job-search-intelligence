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

## Yahoo IMAP transport

Sprint 9 adds a provider-specific Yahoo IMAP transport while retaining the provider-neutral
identity, classification, recruiter, job, and interview pipeline. Authentication requires a Yahoo
app password from process environment variables. The client uses certificate-verified TLS on port
993, exact folder matching, read-only selection, an inclusive server-side `UID SEARCH SINCE`, and
`BODY.PEEK` fetches only. For the approved scope, `2024-07-01` generates
`UID SEARCH SINCE 01-Jul-2024 UID 1:*`; a later checkpoint advances the UID lower bound.

IMAP `SINCE` is evaluated using the Yahoo server's IMAP internal date, not necessarily the
sender-provided `Date` header. The internal date, message header date, and requested since-date are
all retained for audit. A differing header date does not cause local exclusion after the server
returns a UID.

Headers are fetched first. Text/plain is preferred; normalized HTML is used only as a fallback.
For multipart messages, one BODYSTRUCTURE response is parsed locally to identify nested text and
attachment parts, then only the selected text part is fetched. Sequential numbered MIME probing
and attachment-body downloads are prohibited. Malformed or over-complex structures trigger the
bounded fallback; only unrecoverable results enter the failure ledger. Other UIDs continue. HTML is
never executed and links are not opened.

The BODYSTRUCTURE parser accepts quoted strings, escapes, IMAP literals, NIL and numeric values,
nested lists, language/body-location extensions, disposition parameters, and RFC 2231/RFC 2047
parameter encoding. Unexpected syntactically valid extensions are retained as inert structure and
are never interpreted as IMAP status flags.

The stable transport identity is Yahoo provider plus normalized account namespace, exact folder,
UIDVALIDITY, and UID. RFC Message-ID is preserved separately. Checkpoints add the requested
since-date to the provider/account/folder scope and record run timestamps and counts, while UID and
UIDVALIDITY remain the incremental synchronization mechanism. A UIDVALIDITY change stops before
fetch and cannot silently reset the checkpoint. Broken pipes and IMAP aborts discard the dead
connection, authenticate again, reselect the exact folder read-only, verify UIDVALIDITY, and retry
the same UID once. Partial failures leave the checkpoint before the first failed UID.

TLS connection establishment defaults to a 30-second timeout. The underlying IMAP SSL socket is
then explicitly configured with a 60-second read timeout covering login, selection, search, all
header/body/MIME fetches, NOOP, logout, and reconnect. Socket timeouts receive the same bounded
same-UID retry as broken pipes. After retry exhaustion, exactly one UID failure is recorded and the
next UID uses a fresh connection. BODYSTRUCTURE parsing defaults to a 50-part local complexity cap;
exceeding it creates a parsing failure. Progress output contains only operational counters and
UIDs.

When BODYSTRUCTURE cannot be parsed, one bounded `BODY.PEEK[]<0.N>` fallback retrieves at most the
configured maximum plus one detection byte. The 10 MiB default is configurable with
`--max-fallback-message-bytes`. The MIME message is parsed locally; HTML remains inert and
attachment bodies are not requested separately. Oversized fallbacks create exactly one UID failure
and the next UID continues. Reports count BODYSTRUCTURE parse failures, fallback attempts,
successful and failed fallbacks, and oversized fallback messages.

Folder listing and dry-run are read-only. Temporary synchronization requires an explicit database
path at revision `0006`. The CLI refuses the live and legacy database paths without an override;
live synchronization, real credentials, real Yahoo access, and background polling remain outside
Sprint 9.

Count-only mode performs exact read-only folder selection and server-side UID search but fetches no
message headers or bodies and performs no database writes. Search pagination retains the date
condition and advances by highest returned UID plus one. Empty or short pages prove completeness;
repeated, overlapping, or non-monotonic pages stop with `search_complete: false`.

Reports distinguish total matched, batch selected, processed, completed, accepted, and failure
counters. Fetch-efficiency and throughput metrics contain no message or credential content.

## Planned

- Thread reconstruction
- Attachment parsing
- Optional second-stage AI classifier
- Approval-gated live provider synchronization and background polling
