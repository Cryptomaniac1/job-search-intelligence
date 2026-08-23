"""Read-only, evidence-based dashboard analytics.

Application activity uses an explicit application date. Import timestamps are reported
separately and are never treated as application dates. Downstream outcomes require a
deterministic link to a job/application and are deduplicated per job.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPLY_TYPES = {
    "RECRUITER_OUTREACH",
    "RECRUITER_FOLLOW_UP",
    "RECRUITER_REPLY",
    "INTERVIEW_INVITATION",
    "INTERVIEW_CONFIRMATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION",
    "ASSESSMENT_INVITATION",
    "ASSESSMENT_REMINDER",
    "OFFER",
    "OFFER_UPDATE",
    "OFFER_EXPIRED",
    "OFFER_ACCEPTED",
    "OFFER_DECLINED",
}
INTERVIEW_TYPES = {
    "INTERVIEW_INVITATION",
    "INTERVIEW_CONFIRMATION",
    "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION",
    "ASSESSMENT_INVITATION",
    "ASSESSMENT_REMINDER",
}
OFFER_TYPES = {"OFFER", "OFFER_UPDATE", "OFFER_EXPIRED", "OFFER_ACCEPTED", "OFFER_DECLINED"}
REJECTION_TYPES = {"REJECTION", "POSITION_CLOSED"}
METRICS = ("applications", "recruiter_replies", "interviews", "offers", "rejections")


@dataclass(frozen=True)
class ApplicationRecord:
    identity: str
    job_id: int
    job_ids: frozenset[int]
    applied_at: datetime | None
    imported_at: datetime | None
    role_family: str
    company: str


@dataclass(frozen=True)
class OutcomeEvidence:
    job_id: int
    occurred_at: datetime | None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _applications(connection: sqlite3.Connection) -> list[ApplicationRecord]:
    rows = connection.execute(
        """
        SELECT j.id AS job_id, a.id AS application_id,
               COALESCE(a.applied_at, j.applied_at) AS applied_at,
               COALESCE(NULLIF(TRIM(j.role_family), ''), 'Unclassified') AS role_family,
               COALESCE(NULLIF(TRIM(j.company), ''), 'Unknown') AS company,
               j.source, j.email_account, j.confirmation_message_id, j.linkedin_job_id,
               j.first_seen_at
          FROM jobs AS j
          LEFT JOIN applications AS a ON a.job_id = j.id
         WHERE LOWER(COALESCE(a.status, j.status, '')) NOT IN ('new', 'saved', 'withdrawn')
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        message_id = str(row["confirmation_message_id"] or "").strip().casefold()
        account = str(row["email_account"] or row["source"] or "unknown").strip().casefold()
        if row["application_id"] is not None:
            identity = f"application:{row['application_id']}"
        elif message_id:
            identity = f"message:{account}:{message_id}"
        elif row["linkedin_job_id"]:
            identity = f"job:{str(row['source']).casefold()}:{row['linkedin_job_id']}"
        else:
            identity = f"job-row:{row['job_id']}"
        grouped[identity].append(row)
    applications = []
    for identity, candidates in grouped.items():
        representative = candidates[0]
        dates = [_parse_datetime(row["applied_at"]) for row in candidates]
        import_dates = [_parse_datetime(row["first_seen_at"]) for row in candidates]
        applications.append(
            ApplicationRecord(
                identity=identity,
                job_id=int(representative["job_id"]),
                job_ids=frozenset(int(row["job_id"]) for row in candidates),
                applied_at=min((value for value in dates if value), default=None),
                imported_at=min((value for value in import_dates if value), default=None),
                role_family=str(representative["role_family"]),
                company=str(representative["company"]),
            )
        )
    return applications


def _classification_evidence(
    connection: sqlite3.Connection,
) -> tuple[dict[str, list[OutcomeEvidence]], dict[str, dict[str, int]]]:
    evidence: dict[str, list[OutcomeEvidence]] = {
        metric: [] for metric in METRICS if metric != "applications"
    }
    quality: dict[str, dict[str, int]] = {}
    categories = {
        "recruiter_replies": REPLY_TYPES,
        "interviews": INTERVIEW_TYPES,
        "offers": OFFER_TYPES,
        "rejections": REJECTION_TYPES,
    }
    rows = connection.execute(
        """
        SELECT ec.job_id, ec.classification,
               COALESCE(mm.received_at, mm.imap_internal_date) AS occurred_at
          FROM email_classifications AS ec
          LEFT JOIN imap_message_metadata AS mm
            ON mm.message_identity = ec.message_identity
        """
    ).fetchall()
    for metric, types in categories.items():
        selected = [row for row in rows if row["classification"] in types]
        linked = [row for row in selected if row["job_id"] is not None]
        quality[metric] = {
            "evidence_records": len(selected),
            "linked_records": len(linked),
            "unlinked_records": len(selected) - len(linked),
        }
        evidence[metric].extend(
            OutcomeEvidence(int(row["job_id"]), _parse_datetime(row["occurred_at"]))
            for row in linked
        )

    interview_rows = connection.execute(
        "SELECT job_id, occurred_at FROM interview_events"
    ).fetchall()
    evidence["interviews"].extend(
        OutcomeEvidence(int(row["job_id"]), _parse_datetime(row["occurred_at"]))
        for row in interview_rows
        if row["job_id"] is not None
    )
    quality["interviews"]["event_records"] = len(interview_rows)
    quality["interviews"]["unlinked_event_records"] = sum(
        row["job_id"] is None for row in interview_rows
    )

    for row in connection.execute("SELECT job_id, offered_at FROM offers"):
        evidence["offers"].append(
            OutcomeEvidence(int(row["job_id"]), _parse_datetime(row["offered_at"]))
        )
    return evidence, quality


def _deduplicated_evidence(
    evidence: dict[str, list[OutcomeEvidence]], applications: list[ApplicationRecord]
) -> dict[str, dict[str, datetime | None]]:
    job_to_application = {
        job_id: application.identity
        for application in applications
        for job_id in application.job_ids
    }
    result: dict[str, dict[str, datetime | None]] = {}
    for metric, records in evidence.items():
        by_application: dict[str, datetime | None] = {}
        for record in records:
            identity = job_to_application.get(record.job_id)
            if identity is None:
                continue
            previous = by_application.get(identity)
            if previous is None or (record.occurred_at and record.occurred_at < previous):
                by_application[identity] = record.occurred_at
        result[metric] = by_application
    return result


def _period_counts(
    applications: list[ApplicationRecord],
    outcomes: dict[str, dict[str, datetime | None]],
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    values = {metric: 0 for metric in METRICS}
    values["applications"] = sum(
        bool(item.applied_at and start <= item.applied_at < end) for item in applications
    )
    for metric, records in outcomes.items():
        values[metric] = sum(bool(when and start <= when < end) for when in records.values())
    return values


def _rate(numerator: int | float, denominator: int | float, digits: int = 1) -> float:
    return round((numerator / denominator) * 100, digits) if denominator else 0.0


def _change(current: int, baseline: float) -> float | None:
    if baseline == 0:
        return 0.0 if current == 0 else None
    return round(((current - baseline) / baseline) * 100, 1)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(value: datetime) -> datetime:
    return value.replace(year=value.year + (value.month == 12), month=(value.month % 12) + 1)


def _previous_month(value: datetime) -> datetime:
    return value.replace(year=value.year - (value.month == 1), month=((value.month - 2) % 12) + 1)


def _monthly_activity(
    applications: list[ApplicationRecord],
    outcomes: dict[str, dict[str, datetime | None]],
    now: datetime,
    months: int = 26,
) -> list[dict[str, Any]]:
    current_start = _month_start(now)
    first_start = current_start
    for _ in range(months - 1):
        first_start = _previous_month(first_start)
    result: list[dict[str, Any]] = []
    cursor = first_start
    while cursor <= current_start:
        end = min(_next_month(cursor), now) if cursor == current_start else _next_month(cursor)
        counts = _period_counts(applications, outcomes, cursor, end)
        previous_start = _previous_month(cursor)
        if cursor == current_start:
            elapsed = now - cursor
            previous_end = min(previous_start + elapsed, cursor)
            comparison = "previous_month_same_elapsed_days"
        else:
            previous_end = cursor
            comparison = "previous_full_month"
        previous = _period_counts(applications, outcomes, previous_start, previous_end)
        result.append(
            {
                "period": cursor.strftime("%Y-%m"),
                **counts,
                "is_month_to_date": cursor == current_start,
                "comparison_basis": comparison,
                "previous_period": previous,
                "change_percent": {
                    metric: _change(counts[metric], float(previous[metric])) for metric in METRICS
                },
            }
        )
        cursor = _next_month(cursor)
    return result


def analytics_overview(database_path: Path, now: datetime | None = None) -> dict[str, Any]:
    with _connect(database_path) as connection:
        applications = _applications(connection)
        raw_evidence, quality = _classification_evidence(connection)
    outcomes = _deduplicated_evidence(raw_evidence, applications)
    current_time = now or datetime.now(UTC).replace(tzinfo=None)
    current_start = current_time - timedelta(days=30)
    previous_start = current_start - timedelta(days=90)
    current = _period_counts(applications, outcomes, current_start, current_time)
    previous = _period_counts(applications, outcomes, previous_start, current_start)
    monthly_average = {key: round(value / 3, 1) for key, value in previous.items()}
    all_time: dict[str, int | float] = {"applications": len(applications)}
    all_time.update({metric: len(records) for metric, records in outcomes.items()})
    all_time.update(
        {
            "reply_rate": _rate(all_time["recruiter_replies"], all_time["applications"]),
            "interview_rate": _rate(all_time["interviews"], all_time["applications"]),
            "offer_rate": _rate(all_time["offers"], all_time["applications"], 2),
        }
    )
    return {
        "all_time": all_time,
        "last_30_days": current,
        "rolling_windows": {
            f"last_{days}_days": _period_counts(
                applications, outcomes, current_time - timedelta(days=days), current_time
            )
            for days in (30, 60, 90)
        },
        "monthly_activity": _monthly_activity(applications, outcomes, current_time),
        "previous_90_days": previous,
        "previous_90_monthly_average": monthly_average,
        "change_vs_previous_90_monthly_average_percent": {
            key: _change(current[key], monthly_average[key]) for key in METRICS
        },
        "window": {
            "current_start": current_start.isoformat(),
            "current_end": current_time.isoformat(),
            "comparison_start": previous_start.isoformat(),
            "comparison_end": current_start.isoformat(),
        },
        "data_quality": {
            "raw_application_stage_rows": sum(len(item.job_ids) for item in applications),
            "canonical_applications": len(applications),
            "collapsed_duplicate_or_overlapping_rows": sum(
                len(item.job_ids) for item in applications
            )
            - len(applications),
            "undated_applications": sum(item.applied_at is None for item in applications),
            "dated_applications": sum(item.applied_at is not None for item in applications),
            "outcome_evidence": quality,
        },
        "definitions": {
            "applications": (
                "Canonical application identities in an application stage; repeated imports, "
                "overlapping sources, and saved/new/withdrawn records are excluded."
            ),
            "timeline_date": "Explicit applications.applied_at or jobs.applied_at only.",
            "outcomes": (
                "Distinct linked jobs with deterministic evidence; unlinked evidence excluded."
            ),
            "calendar": (
                "Calendar-review event counts are separate and are not application conversions "
                "until deterministically linked."
            ),
        },
    }


def analytics_timeline(database_path: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    with _connect(database_path) as connection:
        applications = _applications(connection)
        raw_evidence, _ = _classification_evidence(connection)
    outcomes = _deduplicated_evidence(raw_evidence, applications)
    buckets: dict[str, dict[str, Any]] = {}

    def bucket(period: str) -> dict[str, Any]:
        return buckets.setdefault(
            period,
            {"period": period, **{metric: 0 for metric in METRICS}, "records_imported": 0},
        )

    for item in applications:
        if item.applied_at:
            bucket(item.applied_at.strftime("%Y-%m"))["applications"] += 1
    for metric, records in outcomes.items():
        for occurred_at in records.values():
            if occurred_at:
                bucket(occurred_at.strftime("%Y-%m"))[metric] += 1
    for item in applications:
        if item.applied_at is None and item.imported_at:
            bucket(item.imported_at.strftime("%Y-%m"))["records_imported"] += 1
    current_time = now or datetime.now(UTC).replace(tzinfo=None)
    if not buckets:
        bucket(current_time.strftime("%Y-%m"))
    cursor = _parse_datetime(f"{min(buckets)}-01")
    assert cursor is not None
    while cursor <= _month_start(current_time):
        bucket(cursor.strftime("%Y-%m"))
        cursor = _next_month(cursor)
    return [buckets[key] for key in sorted(buckets)]


def _grouped_analytics(database_path: Path, attribute: str) -> list[dict[str, Any]]:
    with _connect(database_path) as connection:
        applications = _applications(connection)
        raw_evidence, _ = _classification_evidence(connection)
    outcomes = _deduplicated_evidence(raw_evidence, applications)
    grouped: dict[str, list[ApplicationRecord]] = defaultdict(list)
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    for item in applications:
        label = getattr(item, attribute).strip() or (
            "Unknown" if attribute == "company" else "Unclassified"
        )
        key = label.casefold() if attribute == "company" else label
        labels[key][label] += 1
        grouped[key].append(item)
    result = []
    for key, items in grouped.items():
        identities = {item.identity for item in items}
        job_ids = {job_id for item in items for job_id in item.job_ids}
        row: dict[str, Any] = {
            attribute: labels[key].most_common(1)[0][0],
            "applications": len(items),
            "dated_applications": sum(item.applied_at is not None for item in items),
        }
        for metric, records in outcomes.items():
            row[metric] = len(identities.intersection(records))
        row["reply_rate"] = _rate(row["recruiter_replies"], row["applications"])
        row["interview_rate"] = _rate(row["interviews"], row["applications"])
        row["offer_rate"] = _rate(row["offers"], row["applications"], 2)
        activity = [item.applied_at for item in items if item.applied_at]
        activity.extend(
            occurred
            for records in raw_evidence.values()
            for evidence in records
            if evidence.job_id in job_ids and (occurred := evidence.occurred_at)
        )
        row["last_activity"] = max(activity).isoformat() if activity else None
        result.append(row)
    return sorted(result, key=lambda row: (-row["applications"], str(row[attribute]).casefold()))


def analytics_roles(database_path: Path) -> list[dict[str, Any]]:
    return _grouped_analytics(database_path, "role_family")


def analytics_companies(database_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    return _grouped_analytics(database_path, "company")[:limit]
