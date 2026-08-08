"""Deterministic, provider-agnostic interview evidence extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

EXTRACTOR_VERSION = "deterministic-interview-v1"
INTERVIEW_CLASSIFICATIONS = {
    "INTERVIEW_INVITATION",
    "INTERVIEW_CONFIRMATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION",
    "ASSESSMENT_INVITATION",
    "ASSESSMENT_REMINDER",
}
EVENT_TYPES = {
    "INTERVIEW_INVITATION": "invitation",
    "INTERVIEW_CONFIRMATION": "confirmation",
    "INTERVIEW_RESCHEDULE": "reschedule",
    "INTERVIEW_CANCELLATION": "cancellation",
    "ASSESSMENT_INVITATION": "assessment_invitation",
    "ASSESSMENT_REMINDER": "assessment_reminder",
}
TIMEZONE_OFFSETS = {
    "PST": -8,
    "PDT": -7,
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "UTC": 0,
}
DATE_PATTERNS = (
    (re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (
        re.compile(
            r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)\s+\d{1,2},\s+20\d{2})\b",
            re.I,
        ),
        "%B %d, %Y",
    ),
)
TIME_PATTERN = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b", re.I)
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.I)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
LOCATION_PATTERN = re.compile(r"(?i)\b(?:location|address)\s*:\s*([^.;\n\r]+)")
EVENT_ID_PATTERN = re.compile(
    r"(?im)\b(?:calendar\s+uid|event\s+id|meeting\s+id)\s*:\s*([A-Za-z0-9_.@-]+)"
)
JOB_ID_PATTERN = re.compile(
    r"(?i)\b(?:job|requisition|req)\s*(?:id|#)?\s*[:#-]?\s*([A-Z0-9_-]{4,30})"
)


@dataclass(frozen=True)
class InterviewEvidence:
    event_type: str
    interview_type: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    local_start_text: str
    local_end_text: str
    timezone_text: str
    meeting_url: str
    phone: str
    location_type: str
    location_text: str
    event_identifier: str
    job_identifier: str
    title: str
    matched_signals: tuple[str, ...]
    ambiguity_reasons: tuple[str, ...]
    extractor_version: str = EXTRACTOR_VERSION


def extract_interview(*, classification: str, subject: str, body: str) -> InterviewEvidence | None:
    """Extract only explicit, explainable interview evidence."""
    if classification not in INTERVIEW_CLASSIFICATIONS:
        return None
    text = f"{subject}\n{body}"
    local_start, local_end, zone, start, end, ambiguity = _extract_schedule(text)
    meeting_url = _meeting_url(text)
    phone = _first(PHONE_PATTERN, text)
    location = _first(LOCATION_PATTERN, body)
    signals = _signals(classification, local_start, zone, meeting_url, phone, location)
    return InterviewEvidence(
        event_type=EVENT_TYPES[classification],
        interview_type=_interview_type(text, classification),
        scheduled_start=start,
        scheduled_end=end,
        local_start_text=local_start,
        local_end_text=local_end,
        timezone_text=zone,
        meeting_url=meeting_url,
        phone=phone,
        location_type=_location_type(meeting_url, phone, location),
        location_text=location,
        event_identifier=_first(EVENT_ID_PATTERN, text),
        job_identifier=_first(JOB_ID_PATTERN, text),
        title=subject.strip()[:500],
        matched_signals=tuple(signals),
        ambiguity_reasons=tuple(ambiguity),
    )


def _extract_schedule(
    text: str,
) -> tuple[str, str, str, datetime | None, datetime | None, list[str]]:
    date_value = _extract_date(text)
    times = TIME_PATTERN.findall(text)
    zones = list(
        dict.fromkeys(
            item.upper()
            for item in re.findall(r"\b(?:PST|PDT|EST|EDT|CST|CDT|MST|MDT|UTC)\b", text, re.I)
        )
    )
    zone = zones[0].upper() if len(zones) == 1 else ""
    ambiguity: list[str] = []
    local_start = _local_datetime(date_value, times[0]) if date_value and times else ""
    local_end = _local_datetime(date_value, times[1]) if date_value and len(times) > 1 else ""
    if date_value and times and not local_start:
        ambiguity.append("date or time could not be parsed; schedule omitted")
    if local_start and not zone:
        ambiguity.append("timezone missing or ambiguous; UTC was not fabricated")
    if len(zones) > 1:
        ambiguity.append("multiple timezone abbreviations found")
    start = _to_utc(local_start, zone)
    end = _to_utc(local_end, zone)
    if start and not end:
        duration = re.search(r"\b(\d{1,3})\s*(?:minutes?|mins?)\b", text, re.I)
        if duration:
            end = start + timedelta(minutes=int(duration.group(1)))
            local_end = (
                datetime.fromisoformat(local_start) + timedelta(minutes=int(duration.group(1)))
            ).isoformat()
    return local_start, local_end, zone, start, end, ambiguity


def _extract_date(text: str) -> str:
    for pattern, format_string in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(1)
        if format_string == "%B %d, %Y":
            value = _expand_month(value)
        try:
            return datetime.strptime(value, format_string).date().isoformat()
        except ValueError:
            continue
    return ""


def _expand_month(value: str) -> str:
    abbreviations = {
        "Jan": "January",
        "Feb": "February",
        "Mar": "March",
        "Apr": "April",
        "Jun": "June",
        "Jul": "July",
        "Aug": "August",
        "Sep": "September",
        "Oct": "October",
        "Nov": "November",
        "Dec": "December",
    }
    first, remainder = value.split(" ", 1)
    return f"{abbreviations.get(first[:3].title(), first)} {remainder}"


def _local_datetime(date_value: str, time_value: str) -> str:
    clean_time = re.sub(r"\s+", " ", time_value.strip().upper())
    try:
        parsed_time = datetime.strptime(clean_time, "%I:%M %p" if ":" in clean_time else "%I %p")
    except ValueError:
        return ""
    return f"{date_value}T{parsed_time.time().isoformat()}"


def _to_utc(local_value: str, zone: str) -> datetime | None:
    if not local_value or zone not in TIMEZONE_OFFSETS:
        return None
    local = datetime.fromisoformat(local_value)
    aware = local.replace(tzinfo=timezone(timedelta(hours=TIMEZONE_OFFSETS[zone])))
    return aware.astimezone(UTC).replace(tzinfo=None)


def _meeting_url(text: str) -> str:
    urls = [value.rstrip(".,);]") for value in URL_PATTERN.findall(text)]
    preferred = (
        "zoom.",
        "teams.microsoft.",
        "meet.google.",
        "webex.",
        "hackerrank.",
        "codesignal.",
    )
    return next(
        (value for value in urls if any(domain in value.casefold() for domain in preferred)), ""
    )


def _interview_type(text: str, classification: str) -> str:
    lowered = text.casefold()
    if classification.startswith("ASSESSMENT_") or "assessment" in lowered:
        return "assessment"
    for interview_type, phrases in (
        ("recruiter_screen", ("recruiter screen", "phone screen")),
        ("technical", ("technical interview", "coding interview")),
        ("panel", ("panel interview", "interview panel")),
        ("onsite", ("onsite interview", "on-site interview")),
        ("hiring_manager", ("hiring manager interview", "manager interview")),
        ("final", ("final interview", "final round interview")),
    ):
        if any(phrase in lowered for phrase in phrases):
            return interview_type
    return "interview"


def _location_type(meeting_url: str, phone: str, location: str) -> str:
    if meeting_url:
        return "video"
    if location:
        return "physical"
    if phone:
        return "phone"
    return "unknown"


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1 if match and match.lastindex else 0).strip() if match else ""


def _signals(
    classification: str, local_start: str, zone: str, url: str, phone: str, location: str
) -> list[str]:
    signals = [f"classification={classification}"]
    for label, value in (
        ("schedule", local_start),
        ("timezone", zone),
        ("meeting_url", url),
        ("phone", phone),
        ("location", location),
    ):
        if value:
            signals.append(f"{label}={value}")
    return signals
