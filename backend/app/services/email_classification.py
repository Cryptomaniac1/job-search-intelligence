"""Deterministic, versioned, and explainable email classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .import_identity import normalize_text

CLASSIFIER_VERSION = "deterministic-v1"


class EmailType(str, Enum):
    APPLICATION_CONFIRMATION = "APPLICATION_CONFIRMATION"
    RECRUITER_OUTREACH = "RECRUITER_OUTREACH"
    RECRUITER_FOLLOW_UP = "RECRUITER_FOLLOW_UP"
    RECRUITER_REPLY = "RECRUITER_REPLY"
    INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
    INTERVIEW_CONFIRMATION = "INTERVIEW_CONFIRMATION"
    INTERVIEW_RESCHEDULE = "INTERVIEW_RESCHEDULE"
    INTERVIEW_CANCELLATION = "INTERVIEW_CANCELLATION"
    ASSESSMENT_INVITATION = "ASSESSMENT_INVITATION"
    ASSESSMENT_REMINDER = "ASSESSMENT_REMINDER"
    OFFER = "OFFER"
    OFFER_UPDATE = "OFFER_UPDATE"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_DECLINED = "OFFER_DECLINED"
    REJECTION = "REJECTION"
    POSITION_CLOSED = "POSITION_CLOSED"
    GHOSTING = "GHOSTING"
    NETWORKING = "NETWORKING"
    REFERRAL = "REFERRAL"
    GENERAL_COMPANY_COMMUNICATION = "GENERAL_COMPANY_COMMUNICATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ClassificationResult:
    classification: EmailType
    confidence: float
    reasons: tuple[str, ...]
    classifier_version: str = CLASSIFIER_VERSION


@dataclass(frozen=True)
class Rule:
    classification: EmailType
    phrases: tuple[str, ...]
    confidence: float = 0.98


RULES = (
    Rule(EmailType.OFFER_ACCEPTED, ("offer acceptance confirmed", "accepted your offer")),
    Rule(EmailType.OFFER_DECLINED, ("declined the offer", "offer decline confirmed")),
    Rule(EmailType.OFFER_EXPIRED, ("offer has expired", "offer expired")),
    Rule(EmailType.OFFER_UPDATE, ("updated offer", "revised offer", "offer update")),
    Rule(EmailType.INTERVIEW_CANCELLATION, ("interview has been cancelled", "cancelled interview")),
    Rule(EmailType.INTERVIEW_RESCHEDULE, ("reschedule your interview", "interview rescheduled")),
    Rule(EmailType.INTERVIEW_CONFIRMATION, ("interview is confirmed", "interview confirmation")),
    Rule(EmailType.INTERVIEW_INVITATION, ("schedule your interview", "interview invitation")),
    Rule(
        EmailType.ASSESSMENT_REMINDER,
        ("assessment reminder", "reminder to complete the assessment"),
    ),
    Rule(EmailType.ASSESSMENT_INVITATION, ("complete an assessment", "assessment invitation")),
    Rule(EmailType.REJECTION, ("not moving forward", "decided not to proceed", "other candidates")),
    Rule(EmailType.POSITION_CLOSED, ("position has been closed", "role is no longer available")),
    Rule(
        EmailType.APPLICATION_CONFIRMATION,
        ("thank you for applying", "application received", "successfully applied"),
    ),
    Rule(EmailType.OFFER, ("pleased to offer you", "employment offer", "offer letter")),
    Rule(EmailType.RECRUITER_FOLLOW_UP, ("following up", "checking in regarding"), 0.94),
    Rule(EmailType.RECRUITER_REPLY, ("thanks for getting back", "thank you for your reply"), 0.94),
    Rule(
        EmailType.RECRUITER_OUTREACH,
        ("reaching out about", "your background caught my attention"),
        0.94,
    ),
    Rule(EmailType.REFERRAL, ("referred you", "employee referral", "referral introduction"), 0.96),
    Rule(
        EmailType.NETWORKING,
        ("connect professionally", "networking conversation", "coffee chat"),
        0.92,
    ),
    Rule(
        EmailType.GHOSTING,
        ("no response after multiple follow-ups", "have not heard back after"),
        0.9,
    ),
    Rule(
        EmailType.GENERAL_COMPANY_COMMUNICATION,
        ("company update", "careers newsletter", "talent community"),
        0.86,
    ),
)


def classify_email(*, subject: str, sender: str, body: str) -> ClassificationResult:
    """Classify one message into exactly one canonical business event."""
    fields = {
        "subject": normalize_text(subject),
        "body": normalize_text(body),
        "sender": normalize_text(sender),
    }
    for rule in RULES:
        reasons = tuple(
            f'{field} contains "{phrase}"'
            for phrase in rule.phrases
            for field, value in fields.items()
            if phrase in value
        )
        if reasons:
            return ClassificationResult(rule.classification, rule.confidence, reasons)
    return ClassificationResult(
        EmailType.UNKNOWN,
        0.0,
        ("no deterministic rule matched",),
    )
