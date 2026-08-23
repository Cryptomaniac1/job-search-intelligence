"""Privacy-preserving, deterministic review of interview-like ICS events."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLASSIFIER_VERSION = "calendar-interview-v1"
INTERVIEW_PATTERN = re.compile(
    r"\b(interview|screen|screening call|recruiter call|talent acquisition|"
    r"intro call|introductory interview|onsite prep)\b",
    re.IGNORECASE,
)
EXCLUSION_PATTERN = re.compile(
    r"\b(edd interview|career advising|job search|job termination)\b",
    re.IGNORECASE,
)
REVIEW_PATTERN = re.compile(
    r"\b(phone call|talent team|hiring|recruiter|call for .+ job|chat w/)\b",
    re.IGNORECASE,
)


def _unfold(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _events(path: Path) -> list[dict[str, list[str]]]:
    events: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    for line in _unfold(path):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current.setdefault(key, []).append(value)
    return events


def _property(event: dict[str, list[str]], name: str) -> tuple[str, str] | None:
    for key, values in event.items():
        if key == name or key.startswith(f"{name};"):
            return key, values[0]
    return None


def _start(event: dict[str, list[str]], local_timezone: str) -> datetime | None:
    property_value = _property(event, "DTSTART")
    if property_value is None:
        return None
    key, value = property_value
    if "VALUE=DATE" in key or len(value) == 8:
        return datetime.combine(datetime.strptime(value[:8], "%Y%m%d").date(), time.min)
    try:
        parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    try:
        local_zone: tzinfo = ZoneInfo(local_timezone)
    except ZoneInfoNotFoundError:
        local_zone = UTC
    if value.endswith("Z"):
        return parsed.replace(tzinfo=UTC).astimezone(local_zone).replace(tzinfo=None)
    timezone_match = re.search(r"TZID=([^;:]+)", key)
    if timezone_match:
        try:
            source_zone: tzinfo = ZoneInfo(timezone_match.group(1))
        except ZoneInfoNotFoundError:
            source_zone = local_zone
        return parsed.replace(tzinfo=source_zone).astimezone(local_zone).replace(tzinfo=None)
    return parsed


def _text(event: dict[str, list[str]], name: str) -> str:
    value = _property(event, name)
    if value is None:
        return ""
    return (
        value[1]
        .replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _next_month(value: date) -> date:
    return value.replace(year=value.year + (value.month == 12), month=(value.month % 12) + 1)


def analyze_calendar_interviews(
    path: Path,
    *,
    from_date: date,
    through_date: date,
    local_timezone: str = "America/Los_Angeles",
) -> dict[str, Any]:
    """Return counts only; event content and identities never leave the parser."""
    if not path.is_file():
        raise ValueError(f"ICS file does not exist: {path}")
    monthly: Counter[str] = Counter()
    seen: set[tuple[datetime, str]] = set()
    reviewed = 0
    ambiguous = 0
    excluded = 0
    cancelled = 0
    for event in _events(path):
        started_at = _start(event, local_timezone)
        if started_at is None or not (from_date <= started_at.date() <= through_date):
            continue
        summary = _text(event, "SUMMARY").strip()
        searchable = summary
        status = _text(event, "STATUS").upper()
        if status == "CANCELLED":
            cancelled += 1
            continue
        if EXCLUSION_PATTERN.search(searchable):
            excluded += 1
            continue
        key = (started_at, re.sub(r"\s+", " ", summary.casefold()))
        if key in seen:
            continue
        if INTERVIEW_PATTERN.search(searchable):
            seen.add(key)
            reviewed += 1
            monthly[started_at.strftime("%Y-%m")] += 1
        elif REVIEW_PATTERN.search(searchable):
            ambiguous += 1
    cursor = from_date.replace(day=1)
    end_month = through_date.replace(day=1)
    rows = []
    while cursor <= end_month:
        period = cursor.strftime("%Y-%m")
        rows.append({"period": period, "interviews": monthly[period]})
        cursor = _next_month(cursor)
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "source_type": "ics",
        "from_date": from_date.isoformat(),
        "through_date": through_date.isoformat(),
        "timezone": local_timezone,
        "interview_event_count": reviewed,
        "ambiguous_event_count": ambiguous,
        "excluded_event_count": excluded,
        "cancelled_event_count": cancelled,
        "monthly": rows,
        "privacy": "Counts only; no summaries, attendees, descriptions, or event IDs emitted.",
    }
