"""Read historical email exports and identify deterministic interview evidence."""

from __future__ import annotations

import json
import mailbox
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .email_classification import RULES, ClassificationResult, classify_email
from .import_identity import normalize_text, stable_message_identity
from .interview_pipeline import INTERVIEW_CLASSIFICATIONS, InterviewEvidence, extract_interview

SUPPORTED_PROVIDERS = {"gmail", "hotmail", "yahoo"}


@dataclass(frozen=True)
class HistoricalMessage:
    """Provider-neutral historical message content needed for deterministic replay."""

    provider: str
    source_name: str
    message_id: str
    subject: str
    sender: str
    body: str
    received_at: datetime | None


@dataclass(frozen=True)
class HistoricalInterviewCandidate:
    """A message that passed deterministic classification and extraction."""

    message: HistoricalMessage
    identity: str
    classification: ClassificationResult
    evidence: InterviewEvidence


@dataclass(frozen=True)
class HistoricalMessageAnalysis:
    """Explain whether a historical message is safe to replay."""

    status: str
    matched_classifications: tuple[str, ...]
    candidate: HistoricalInterviewCandidate | None


def iter_mbox_messages(path: Path, provider: str) -> Iterator[HistoricalMessage]:
    """Yield normalized Gmail or Hotmail messages from an MBOX export."""
    normalized_provider = provider.casefold().strip()
    if normalized_provider not in {"gmail", "hotmail"}:
        raise ValueError("MBOX provider must be gmail or hotmail")
    archive = mailbox.mbox(path, create=False)
    try:
        for message in archive:
            yield HistoricalMessage(
                provider=normalized_provider,
                source_name=path.name,
                message_id=_decode_header(message.get("Message-ID")),
                subject=_decode_header(message.get("Subject")),
                sender=_decode_header(message.get("From")),
                body=_message_body(message),
                received_at=_parse_date(message.get("Date")),
            )
    finally:
        archive.close()


def iter_yahoo_messages(path: Path) -> Iterator[HistoricalMessage]:
    """Yield raw-message records from a Yahoo JSON export."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Yahoo export must be a list or contain a records list")
    for record in records:
        if not isinstance(record, dict):
            continue
        subject = _string(record.get("subject"))
        sender = _string(record.get("sender"))
        body = _string(record.get("body"))
        if not any((subject, sender, body)):
            continue
        yield HistoricalMessage(
            provider="yahoo",
            source_name=path.name,
            message_id=_string(record.get("confirmation_message_id") or record.get("message_id")),
            subject=subject,
            sender=sender,
            body=body,
            received_at=_parse_iso_date(record.get("applied_at") or record.get("received_at")),
        )


def build_interview_candidate(
    message: HistoricalMessage,
) -> HistoricalInterviewCandidate | None:
    """Return deterministic interview evidence and ignore all other messages."""
    return analyze_historical_message(message).candidate


def analyze_historical_message(message: HistoricalMessage) -> HistoricalMessageAnalysis:
    """Classify one message and expose skipped or conflicting evidence."""
    if message.provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {message.provider}")
    matched_types = tuple(sorted(_matched_interview_types(message)))
    if len(matched_types) > 1:
        return HistoricalMessageAnalysis("conflicting", matched_types, None)
    classification = classify_email(
        subject=message.subject,
        sender=message.sender,
        body=message.body,
    )
    if matched_types != (classification.classification.value,):
        return HistoricalMessageAnalysis("skipped", matched_types, None)
    evidence = extract_interview(
        classification=classification.classification.value,
        subject=message.subject,
        body=message.body,
    )
    if evidence is None:
        return HistoricalMessageAnalysis("skipped", matched_types, None)
    identity = stable_message_identity(
        provider=message.provider,
        message_id=message.message_id,
        subject=message.subject,
        sender=message.sender,
        received_at=message.received_at,
        body=message.body,
    )
    candidate = HistoricalInterviewCandidate(message, identity, classification, evidence)
    return HistoricalMessageAnalysis("supported", matched_types, candidate)


def _matched_interview_types(message: HistoricalMessage) -> set[str]:
    fields = (
        normalize_text(message.subject),
        normalize_text(message.sender),
        normalize_text(message.body),
    )
    return {
        rule.classification.value
        for rule in RULES
        if rule.classification.value in INTERVIEW_CLASSIFICATIONS
        and any(phrase in value for phrase in rule.phrases for value in fields)
    }


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def _message_body(message: Message) -> str:
    parts: list[str] = []
    candidates = message.walk() if message.is_multipart() else (message,)
    for part in candidates:
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition.casefold():
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            parts.append(payload.decode(charset, errors="replace"))
        except LookupError:
            parts.append(payload.decode("utf-8", errors="replace"))
    text = re.sub(r"<[^>]+>", " ", "\n".join(parts))
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _parse_iso_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
