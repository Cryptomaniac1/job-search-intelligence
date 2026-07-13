"""Deterministic recruiter extraction and company normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

RECRUITER_CLASSIFICATIONS = {
    "RECRUITER_OUTREACH",
    "RECRUITER_REPLY",
    "RECRUITER_FOLLOW_UP",
}
INTERVIEW_CONTACT_CLASSIFICATIONS = {
    "INTERVIEW_INVITATION",
    "INTERVIEW_CONFIRMATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION",
    "ASSESSMENT_INVITATION",
    "ASSESSMENT_REMINDER",
}
GENERIC_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "icloud.com",
}
GENERIC_LOCAL_PARTS = {"careers", "jobs", "noreply", "no-reply", "notifications"}
LEGAL_SUFFIXES = re.compile(
    r"\b(?:incorporated|inc|llc|ltd|limited|corp|corporation|co|company)\b\.?",
    re.IGNORECASE,
)
COMPANY_PATTERN = re.compile(
    r"\b(?:at|with|from)\s+([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4})"
    r"(?=[,.;\n]|$)"
)
LINKEDIN_PATTERN = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.I)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
TITLE_PATTERN = re.compile(
    r"(?im)^\s*((?:senior |lead |principal )?(?:technical )?"
    r"(?:recruiter|sourcer|talent partner|talent acquisition|recruiting coordinator))\s*$"
)
RECRUITING_ROLE_PATTERN = re.compile(
    r"\b(?:recruiter|sourcer|talent partner|talent acquisition|recruiting coordinator)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RecruiterEvidence:
    name: str
    email: str
    normalized_email: str
    company: str
    normalized_company: str
    title: str
    signature: str
    linkedin_url: str
    phone: str
    confidence: float
    relationship_type: str


def normalize_company(value: str) -> str:
    """Create a stable comparison key without creating a Company entity."""
    normalized = LEGAL_SUFFIXES.sub(" ", value.casefold())
    normalized = re.sub(r"[^a-z0-9&]+", " ", normalized)
    return " ".join(normalized.split())


def extract_recruiter(
    *, classification: str, sender: str, subject: str, body: str
) -> RecruiterEvidence | None:
    """Return recruiter evidence only when deterministic minimums are satisfied."""
    supported = RECRUITER_CLASSIFICATIONS | INTERVIEW_CONTACT_CLASSIFICATIONS
    if classification not in supported:
        return None
    name, address = parseaddr(sender)
    normalized_email = address.strip().casefold()
    if not _eligible_email(normalized_email):
        return None
    company = _extract_company(subject, body, normalized_email)
    normalized_company = normalize_company(company)
    if not normalized_company:
        return None
    clean_name = " ".join(name.strip().split())
    if classification in INTERVIEW_CONTACT_CLASSIFICATIONS and not RECRUITING_ROLE_PATTERN.search(
        f"{clean_name}\n{body}"
    ):
        return None
    confidence = 0.95 if clean_name else 0.9
    return RecruiterEvidence(
        name=clean_name,
        email=address.strip(),
        normalized_email=normalized_email,
        company=company,
        normalized_company=normalized_company,
        title=_first_match(TITLE_PATTERN, body),
        signature=_signature(body),
        linkedin_url=_first_match(LINKEDIN_PATTERN, body),
        phone=_first_match(PHONE_PATTERN, body),
        confidence=confidence,
        relationship_type=_relationship_type(body),
    )


def _eligible_email(address: str) -> bool:
    if "@" not in address:
        return False
    local_part, _ = address.rsplit("@", 1)
    return local_part not in GENERIC_LOCAL_PARTS


def _extract_company(subject: str, body: str, email: str) -> str:
    explicit = COMPANY_PATTERN.search(f"{subject}\n{body}")
    if explicit:
        return explicit.group(1).strip(" ,.")
    domain = email.rsplit("@", 1)[1]
    if domain in GENERIC_DOMAINS:
        return ""
    label = domain.split(".")[0].replace("-", " ")
    return label.title()


def _first_match(pattern: re.Pattern[str], value: str) -> str:
    match = pattern.search(value)
    if not match:
        return ""
    return match.group(1 if match.lastindex else 0).strip()


def _signature(body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(lines[-6:])[:2000]


def _relationship_type(body: str) -> str:
    lowered = body.casefold()
    if "sourcer" in lowered:
        return "sourcer"
    if "coordinator" in lowered:
        return "coordinator"
    if "hiring contact" in lowered or "hiring manager" in lowered:
        return "hiring_contact"
    return "unknown"
