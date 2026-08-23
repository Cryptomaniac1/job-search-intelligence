from __future__ import annotations

from datetime import date
from pathlib import Path

from backend.app.services.calendar_analytics import analyze_calendar_interviews


def test_calendar_review_counts_months_deduplicates_and_excludes_content(tmp_path: Path) -> None:
    source = tmp_path / "calendar.ics"
    source.write_text(
        """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/Los_Angeles:20240708T140000
SUMMARY:Interview with Example
DESCRIPTION:Private notes
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/Los_Angeles:20240708T140000
SUMMARY:Interview with Example
END:VEVENT
BEGIN:VEVENT
DTSTART:20240801T180000Z
SUMMARY:Recruiter Screen
END:VEVENT
BEGIN:VEVENT
DTSTART:20240802T180000Z
SUMMARY:CA EDD Interview
END:VEVENT
BEGIN:VEVENT
DTSTART:20240803T180000Z
SUMMARY:Possible hiring conversation
END:VEVENT
BEGIN:VEVENT
DTSTART:20240804T180000Z
SUMMARY:Interview cancelled
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )
    result = analyze_calendar_interviews(
        source, from_date=date(2024, 7, 1), through_date=date(2024, 9, 30)
    )
    assert result["interview_event_count"] == 2
    assert result["ambiguous_event_count"] == 1
    assert result["excluded_event_count"] == 1
    assert result["cancelled_event_count"] == 1
    assert result["monthly"] == [
        {"period": "2024-07", "interviews": 1},
        {"period": "2024-08", "interviews": 1},
        {"period": "2024-09", "interviews": 0},
    ]
    rendered = str(result)
    assert "Private notes" not in rendered
    assert "Example" not in rendered
