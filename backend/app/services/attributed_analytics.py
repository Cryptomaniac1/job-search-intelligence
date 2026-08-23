"""Build and read a provenance-bearing external analytics snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .application_attribution import infer_application_role_family
from .calendar_analytics import EXCLUSION_PATTERN, INTERVIEW_PATTERN, _events, _start, _text

SNAPSHOT_VERSION = "attributed-analytics-v1"
XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
ACCOUNT_MAP = {
    "Yahoo": ("solovat@yahoo.com", "Product Management / TPM"),
    "Hotmail": ("solovat@hotmail.com", "Marketing"),
    "soultanovr Gmail": ("soultanovr@gmail.com", "Marketing"),
    "ibuildanapp Gmail": ("ibuildanapp@gmail.com", "Operations / Sales Engineering"),
    "solovat Gmail": ("solovat@gmail.com", "Product Management / TPM"),
}
STAGE_PATTERNS = {
    "final": re.compile(r"\b(final|finalist|executive interview)\b", re.I),
    "hm_team": re.compile(r"\b(hiring manager|hm interview|team interview|panel)\b", re.I),
    "recruiter": re.compile(
        r"\b(recruiter|phone screen|screening|talent acquisition|intro call)\b", re.I
    ),
}
ROLE_PATTERNS = {
    "Fleet / Field Operations": re.compile(
        r"\b(?:vehicle operator|fleet operations?|field operations?|fleet management)\b", re.I
    ),
    "Product Management": re.compile(
        r"\b(product manager|product management|product growth)\b", re.I
    ),
    "TPM / Program": re.compile(r"\b(technical program|technical project|program manager)\b", re.I),
    "Lifecycle / CRM": re.compile(r"\b(lifecycle|crm|retention|email marketing)\b", re.I),
    "GTM / Sales Engineering": re.compile(
        r"\b(sales engineer|solutions? architect|technical solutions?|gtm)\b", re.I
    ),
    "Performance Marketing": re.compile(r"\b(performance marketing|paid media|sem)\b", re.I),
    "Demand Generation": re.compile(r"\b(demand gen|demand generation|abm)\b", re.I),
    "Growth Marketing": re.compile(r"\b(growth marketing|growth manager)\b", re.I),
    "Delivery / Operations": re.compile(r"\b(delivery|operations|driver)\b", re.I),
    "Product Marketing": re.compile(r"\bproduct marketing\b", re.I),
}
GENERIC_COMPANY_PATTERN = re.compile(
    r"\b(interview|manager|marketing|engineer|engineering|product|recruiter|"
    r"enterprise|position|role|your|unknown|workday|myworkday|hiring|digital|landing|"
    r"candidate|candidates|recruiting|recruitment|careers?|application|status|confirmed|"
    r"confidential|talent|director|message|thanks|forward|greenhouse)\b",
    re.I,
)
MEETING_PLATFORM_NAMES = {"google", "google meet", "teams", "zoom", "phone", "video"}
GENERIC_COMPANY_NAMES = {
    "app",
    "ats",
    "com",
    "email",
    "gmail",
    "join",
    "move",
    "msg",
    "update",
    "zoom video",
}
PROVIDER_ACCOUNT_MAP = {
    "yahoo": ("solovat@yahoo.com", "Product Management / TPM"),
    "hotmail": ("solovat@hotmail.com", "Marketing"),
    "gmail": ("soultanovr@gmail.com", "Marketing"),
}
ACCOUNT_NAMESPACE_MAP = {
    "solovat@gmail.com": ("solovat@gmail.com", "Product Management / TPM"),
    "soultanovr@gmail.com": ("soultanovr@gmail.com", "Marketing"),
    "ibuildanapp@gmail.com": ("ibuildanapp@gmail.com", "Operations / Sales Engineering"),
}
OUTCOME_GROUPS = {
    "recruiter_replies": {
        "RECRUITER_OUTREACH",
        "RECRUITER_REPLY",
        "RECRUITER_FOLLOW_UP",
    },
    "interviews": {
        "INTERVIEW_INVITATION",
        "INTERVIEW_CONFIRMATION",
        "INTERVIEW_RESCHEDULE",
        "INTERVIEW_CANCELLATION",
        "ASSESSMENT_INVITATION",
        "ASSESSMENT_REMINDER",
    },
    "offers": {
        "OFFER",
        "OFFER_UPDATE",
        "OFFER_EXPIRED",
        "OFFER_ACCEPTED",
        "OFFER_DECLINED",
    },
    "rejections": {"REJECTION", "POSITION_CLOSED"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str | float | None:
    value = cell.find("x:v", XML_NS)
    if value is None or value.text is None:
        return None
    if cell.get("t") == "s":
        return shared[int(value.text)]
    try:
        return float(value.text)
    except ValueError:
        return value.text


def _xlsx_rows(path: Path) -> list[tuple[date, int]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in root.findall("x:si", XML_NS)]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result: list[tuple[date, int]] = []
    for row in sheet.findall(".//x:sheetData/x:row", XML_NS)[1:]:
        values: dict[str, str | float | None] = {}
        for cell in row:
            column = re.match(r"[A-Z]+", cell.get("r", ""))
            if column:
                values[column.group()] = _cell_value(cell, shared)
        serial, count = values.get("A"), values.get("B")
        if isinstance(serial, float) and isinstance(count, float):
            result.append(((datetime(1899, 12, 30) + timedelta(days=serial)).date(), int(count)))
    return result


def _plan_activity(path: Path) -> dict[str, Any]:
    rows = _xlsx_rows(path)
    monthly: dict[str, int] = defaultdict(int)
    active_days: Counter[str] = Counter()
    for occurred_on, count in rows:
        period = occurred_on.strftime("%Y-%m")
        monthly[period] += count
        active_days[period] += int(count > 0)
    first, last = min(day for day, _ in rows), max(day for day, _ in rows)
    cursor = first.replace(day=1)
    missing = []
    while cursor <= last.replace(day=1):
        period = cursor.strftime("%Y-%m")
        if period not in monthly:
            missing.append(period)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    positive = [count for _, count in rows if count > 0]
    monthly_rows = []
    for period, count in sorted(monthly.items()):
        prior_period = _prior_period(period)
        prior = monthly.get(prior_period)
        comparison_label = prior_period
        if period == last.strftime("%Y-%m") and last.day < monthrange(last.year, last.month)[1]:
            prior = sum(
                value
                for occurred_on, value in rows
                if occurred_on.strftime("%Y-%m") == prior_period and occurred_on.day <= last.day
            )
            comparison_label = f"{prior_period} through day {last.day}"
        monthly_rows.append(
            {
                "period": period,
                "applications": count,
                "active_days": active_days[period],
                "change_percent": _change_percent(count, prior),
                "comparison_label": comparison_label if prior is not None else None,
            }
        )
    return {
        "source": "manual_outbound_application_log",
        "coverage_start": first.isoformat(),
        "coverage_end": last.isoformat(),
        "recorded_applications": sum(count for _, count in rows),
        "active_day_average": round(sum(positive) / len(positive), 1),
        "missing_months": missing,
        "daily": [
            {"date": occurred_on.isoformat(), "applications": count} for occurred_on, count in rows
        ],
        "monthly": monthly_rows,
    }


def _docx_tables(path: Path) -> list[list[list[str]]]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    tables: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", WORD_NS):
        rows = []
        for row in table.findall("w:tr", WORD_NS):
            rows.append(["".join(cell.itertext()).strip() for cell in row.findall("w:tc", WORD_NS)])
        tables.append(rows)
    return tables


def _integer(value: str) -> int:
    return int(value.replace(",", "").strip())


def _percent(value: str) -> float:
    return float(value.replace("%", "").strip())


def _funnel(path: Path) -> dict[str, Any]:
    tables = _docx_tables(path)
    roles = [
        {
            "role_family": row[0],
            "applications": _integer(row[1]),
            "hm_team": _integer(row[2]),
            "hm_rate": _percent(row[3]),
            "finals": _integer(row[4]),
            "final_rate": _percent(row[5]),
            "offers": _integer(row[6]),
        }
        for row in tables[0][1:]
    ]
    accounts = []
    for row in tables[1][1:]:
        account, default_role = ACCOUNT_MAP[row[0]]
        accounts.append(
            {
                "source_label": row[0],
                "account": account,
                "default_role_family": default_role,
                "applications": _integer(row[1]),
                "hm_team": _integer(row[2]),
                "hm_rate": _percent(row[3]),
                "finals": _integer(row[4]),
                "final_rate": _percent(row[5]),
            }
        )
    return {
        "applications": sum(row["applications"] for row in accounts),
        "hm_team": sum(row["hm_team"] for row in accounts),
        "finals": sum(row["finals"] for row in accounts),
        "offers": sum(row["offers"] for row in roles),
        "by_account": accounts,
        "by_role": roles,
    }


def _company_vocabulary(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT DISTINCT TRIM(company) FROM jobs").fetchall()
    values = {
        str(row[0]).strip()
        for row in rows
        if len(str(row[0]).strip()) >= 3
        and not GENERIC_COMPANY_PATTERN.search(str(row[0]))
        and str(row[0]).strip().casefold() not in MEETING_PLATFORM_NAMES
    }
    return sorted(values, key=len, reverse=True)


def _matched_company(summary: str, companies: list[str]) -> str | None:
    patterns = (
        r"interview with ([^|–—:-]+)",
        r"interview (?:at|with recruiter at) ([^|–—:-]+)",
        r"phone screen at ([^|–—:-]+)",
    )
    for pattern in patterns:
        found = re.search(pattern, summary, re.I)
        if found:
            candidate = _normalize_company_candidate(found.group(1))
            if (
                candidate
                and "rafael" not in candidate.casefold()
                and not GENERIC_COMPANY_PATTERN.search(candidate)
                and candidate.casefold() not in MEETING_PLATFORM_NAMES
            ):
                return candidate
    prefix = re.match(
        r"^(.{2,50}?)\s+(?:virtual\s+|video\s+|phone\s+|teams\s+|google meet\s+)?"
        r"(?:interview|recruiter call|phone screen)",
        summary,
        re.I,
    )
    if prefix:
        candidate = _normalize_company_candidate(prefix.group(1))
        if (
            not GENERIC_COMPANY_PATTERN.search(candidate)
            and candidate.casefold() not in MEETING_PLATFORM_NAMES
            and "rafael" not in candidate.casefold()
        ):
            return candidate
    normalized = summary.casefold()
    for company in companies:
        if not _contains_company_name(normalized, company):
            continue
        company_pattern = re.escape(company.casefold())
        contextual = (
            rf"(?<!\w){company_pattern}(?!\w).{{0,35}}\b(interview|screen|recruiter call)\b|"
            rf"\b(interview|screen|recruiter call)\b.{{0,35}}\b(with|at|from|and)\s+"
            rf"{company_pattern}(?!\w)"
        )
        if re.search(contextual, normalized):
            return company
    return None


def _contains_company_name(text: str, company: str) -> bool:
    """Match a company as a bounded text token before compiling contextual regexes."""
    candidate = company.casefold()
    offset = text.find(candidate)
    while offset >= 0:
        end = offset + len(candidate)
        before = text[offset - 1] if offset else ""
        after = text[end] if end < len(text) else ""
        if not (before.isalnum() or after.isalnum()):
            return True
        offset = text.find(candidate, offset + 1)
    return False


def _normalize_company_candidate(value: str) -> str:
    candidate = re.sub(r"\b(?:inc\.?|interview)\b.*$", "", value, flags=re.I)
    candidate = re.sub(r"\s+(?:teams|gms)$", "", candidate, flags=re.I)
    return candidate.strip(" ,.-|–—:")


def _role_family(summary: str) -> str:
    specific_role = infer_application_role_family(summary)
    if specific_role:
        return specific_role
    for family, pattern in ROLE_PATTERNS.items():
        if pattern.search(summary):
            return family
    return "Unknown"


def _stage(summary: str) -> str:
    for stage, pattern in STAGE_PATTERNS.items():
        if pattern.search(summary):
            return stage
    return "unknown"


def _calendar_attribution(path: Path, database_path: Path, through_date: date) -> dict[str, Any]:
    companies = _company_vocabulary(database_path)
    monthly: Counter[str] = Counter()
    by_company: dict[str, Counter[str]] = defaultdict(Counter)
    by_role: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[datetime, str]] = set()
    attributed_company = attributed_role = 0
    event_dates: list[date] = []
    for event in _events(path):
        started_at = _start(event, "America/Los_Angeles")
        summary = _text(event, "SUMMARY").strip()
        evidence = f"{summary}\n{_text(event, 'DESCRIPTION').strip()}"
        if not started_at or not (date(2024, 7, 1) <= started_at.date() <= through_date):
            continue
        if _text(event, "STATUS").upper() == "CANCELLED":
            continue
        if EXCLUSION_PATTERN.search(evidence) or not INTERVIEW_PATTERN.search(evidence):
            continue
        key = (started_at, re.sub(r"\s+", " ", summary.casefold()))
        if key in seen:
            continue
        seen.add(key)
        stage = _stage(evidence)
        company = _matched_company(evidence, companies) or "Unattributed"
        role = _role_family(evidence)
        monthly[started_at.strftime("%Y-%m")] += 1
        event_dates.append(started_at.date())
        by_company[company][stage] += 1
        by_role[role][stage] += 1
        attributed_company += company != "Unattributed"
        attributed_role += role != "Unknown"
    return {
        "event_count": len(seen),
        "company_attributed_count": attributed_company,
        "role_attributed_count": attributed_role,
        "monthly": _calendar_monthly(monthly, event_dates, through_date),
        "by_company": _counter_rows(by_company, "company"),
        "by_role": _counter_rows(by_role, "role_family"),
    }


def _calendar_monthly(
    monthly: Counter[str], event_dates: list[date], through_date: date
) -> list[dict[str, Any]]:
    rows = []
    for period, count in sorted(monthly.items()):
        prior_period = _prior_period(period)
        prior = monthly.get(prior_period)
        comparison_label = prior_period
        if period == through_date.strftime("%Y-%m"):
            prior = sum(
                occurred_on.strftime("%Y-%m") == prior_period
                and occurred_on.day <= through_date.day
                for occurred_on in event_dates
            )
            comparison_label = f"{prior_period} through day {through_date.day}"
        rows.append(
            {
                "period": period,
                "events": count,
                "change_percent": _change_percent(count, prior),
                "comparison_label": comparison_label if prior is not None else None,
            }
        )
    return rows


def _prior_period(period: str) -> str:
    year, month = (int(value) for value in period.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _change_percent(current: int, previous: int | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _email_evidence(database_path: Path) -> dict[str, Any]:
    """Aggregate bounded synchronized evidence without emitting message content."""
    companies = _company_vocabulary(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"imap_message_metadata", "email_classifications"}.issubset(tables):
            return {
                "message_count": 0,
                "linked_job_count": 0,
                "company_attributed_count": 0,
                "role_explicit_count": 0,
                "role_account_default_count": 0,
                "unresolved_company_count": 0,
                "unique_application_confirmation_count": 0,
                "application_confirmations_monthly": [],
                "application_confirmations_daily": [],
                "role_attributed_resume_submission_count": 0,
                "unresolved_resume_submission_count": 0,
                "resume_submissions_by_role": [],
                "resume_submissions_monthly_by_role": [],
                "observed_outcomes_monthly": [],
                "linked_outcomes_monthly": [],
                "unlinked_outcome_count": 0,
                "by_account": [],
                "by_company": [],
                "by_role": [],
            }
        metadata_columns = {
            str(column[1])
            for column in connection.execute("PRAGMA table_info(imap_message_metadata)")
        }
        account_column = "m.account_namespace" if "account_namespace" in metadata_columns else "''"
        reviewed_links_available = "evidence_job_links" in tables
        review_join = (
            "LEFT JOIN evidence_job_links AS r ON r.message_identity = c.message_identity"
            if reviewed_links_available
            else ""
        )
        effective_job_id = (
            "COALESCE(c.job_id, r.job_id)" if reviewed_links_available else "c.job_id"
        )
        rows = connection.execute(
            f"""
            SELECT m.provider, {account_column} AS account_namespace,
                   m.message_identity, m.subject, m.text_body,
                   m.imap_internal_date, m.received_at,
                   c.classification, {effective_job_id} AS job_id,
                   j.company, j.title, j.role_family
            FROM imap_message_metadata AS m
            JOIN email_classifications AS c ON c.message_identity = m.message_identity
            {review_join}
            LEFT JOIN jobs AS j ON j.id = {effective_job_id}
            ORDER BY COALESCE(m.imap_internal_date, m.received_at),
                     m.provider, m.message_identity
            """
        ).fetchall()
        mbox_rows = []
        if {"email_imports", "imported_messages", "jobs"}.issubset(tables):
            mbox_rows = connection.execute(
                """
                SELECT i.stable_message_identity, c.classification, c.job_id,
                       j.applied_at, j.email_account, j.company, j.title, j.role_family
                FROM email_imports AS e
                JOIN imported_messages AS i ON i.source_import_id = e.id
                JOIN email_classifications AS c ON c.message_identity = i.stable_message_identity
                LEFT JOIN jobs AS j ON j.id = c.job_id
                WHERE e.source_filename LIKE '%.mbox'
                """
            ).fetchall()
    accounts: dict[str, Counter[str]] = defaultdict(Counter)
    company_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    company_attributed = role_explicit = role_defaulted = linked_jobs = 0
    application_confirmations: dict[str, dict[str, str]] = {}
    linked_outcomes: dict[tuple[str, str], str] = {}
    observed_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    unlinked_outcomes = 0
    for row in rows:
        provider = str(row["provider"]).casefold()
        account, default_role = ACCOUNT_NAMESPACE_MAP.get(
            str(row["account_namespace"] or "").casefold(),
            PROVIDER_ACCOUNT_MAP.get(provider, (provider, "Unmapped")),
        )
        classification = str(row["classification"])
        occurred_at = str(row["imap_internal_date"] or row["received_at"] or "")
        period = occurred_at[:7] if re.fullmatch(r"\d{4}-\d{2}.*", occurred_at) else ""
        accounts[account][classification] += 1
        linked_jobs += row["job_id"] is not None
        if classification == "APPLICATION_CONFIRMATION":
            if period:
                identity = (
                    f'job:{row["job_id"]}'
                    if row["job_id"] is not None
                    else f'message:{row["message_identity"]}'
                )
                application_confirmations.setdefault(
                    identity,
                    {"period": period, "date": occurred_at[:10], "account": account},
                )
        outcome_group = _outcome_group(classification)
        if outcome_group and period:
            observed_outcomes[period][outcome_group] += 1
            if row["job_id"] is None:
                unlinked_outcomes += 1
            else:
                linked_outcomes.setdefault((f'job:{row["job_id"]}', outcome_group), period)
        text = f'{row["subject"] or ""}\n{row["text_body"] or ""}'
        linked_company = str(row["company"] or "").strip()
        company = (
            linked_company
            if _valid_company(linked_company)
            else _company_from_text(text, companies)
        )
        role = str(row["role_family"] or "").strip()
        if not role or role.casefold() in {"unknown", "unclassified"}:
            role = _role_family(f'{row["title"] or ""}\n{text}')
        if role != "Unknown":
            role_explicit += 1
        elif default_role != "Unmapped":
            role = f"{default_role} (account default)"
            role_defaulted += 1
        if company:
            company_counts[company] += 1
            company_attributed += 1
        if role != "Unknown":
            role_counts[role] += 1
    for row in mbox_rows:
        account, default_role = ACCOUNT_NAMESPACE_MAP.get(
            str(row["email_account"] or "").casefold(),
            (str(row["email_account"] or "gmail"), "Unmapped"),
        )
        classification = str(row["classification"])
        accounts[account][classification] += 1
        linked_jobs += row["job_id"] is not None
        if classification != "APPLICATION_CONFIRMATION" or row["job_id"] is None:
            continue
        occurred_at = str(row["applied_at"] or "")
        if not re.fullmatch(r"\d{4}-\d{2}.*", occurred_at):
            continue
        identity = f'job:{row["job_id"]}'
        application_confirmations.setdefault(
            identity,
            {"period": occurred_at[:7], "date": occurred_at[:10], "account": account},
        )
        linked_company = str(row["company"] or "").strip()
        if _valid_company(linked_company):
            company_counts[linked_company] += 1
            company_attributed += 1
        role = str(row["role_family"] or "").strip()
        role = role or infer_application_role_family(str(row["title"] or "")) or default_role
        if role != "Unmapped":
            role_counts[role] += 1
        if classification == "APPLICATION_CONFIRMATION" and row["job_id"] is not None:
            application_confirmations[identity]["role_family"] = role
    return {
        "message_count": len(rows) + len(mbox_rows),
        "imap_message_count": len(rows),
        "mbox_message_count": len(mbox_rows),
        "linked_job_count": linked_jobs,
        "company_attributed_count": company_attributed,
        "role_explicit_count": role_explicit,
        "role_account_default_count": role_defaulted,
        "unresolved_company_count": len(rows) - company_attributed,
        "unique_application_confirmation_count": len(application_confirmations),
        "application_confirmations_monthly": _confirmation_monthly(application_confirmations),
        "application_confirmations_daily": _confirmation_daily(application_confirmations),
        "role_attributed_resume_submission_count": sum(
            bool(evidence.get("role_family")) for evidence in application_confirmations.values()
        ),
        "unresolved_resume_submission_count": sum(
            not bool(evidence.get("role_family")) for evidence in application_confirmations.values()
        ),
        "resume_submissions_by_role": _confirmation_by_role(application_confirmations),
        "resume_submissions_monthly_by_role": _confirmation_monthly_by_role(
            application_confirmations
        ),
        "observed_outcomes_monthly": _observed_outcome_monthly(observed_outcomes),
        "linked_outcomes_monthly": _outcome_monthly(linked_outcomes),
        "unlinked_outcome_count": unlinked_outcomes,
        "by_account": [
            {
                "account": account,
                "messages": sum(counts.values()),
                "classifications": dict(sorted(counts.items())),
            }
            for account, counts in sorted(accounts.items())
        ],
        "by_company": [
            {"company": company, "messages": count}
            for company, count in company_counts.most_common(50)
        ],
        "by_role": [
            {"role_family": role, "messages": count} for role, count in role_counts.most_common()
        ],
    }


def _outcome_group(classification: str) -> str | None:
    for group, classifications in OUTCOME_GROUPS.items():
        if classification in classifications:
            return group
    return None


def _outcome_monthly(outcomes: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    monthly: dict[str, Counter[str]] = defaultdict(Counter)
    for (_, group), period in outcomes.items():
        monthly[period][group] += 1
    return [
        {
            "period": period,
            **{group: counts[group] for group in OUTCOME_GROUPS},
        }
        for period, counts in sorted(monthly.items())
    ]


def _observed_outcome_monthly(
    outcomes: dict[str, Counter[str]],
) -> list[dict[str, Any]]:
    return [
        {"period": period, **{group: counts[group] for group in OUTCOME_GROUPS}}
        for period, counts in sorted(outcomes.items())
    ]


def _confirmation_monthly(
    confirmations: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    by_month: Counter[str] = Counter()
    by_month_account: dict[str, Counter[str]] = defaultdict(Counter)
    for evidence in confirmations.values():
        period = evidence["period"]
        account = evidence["account"]
        by_month[period] += 1
        by_month_account[period][account] += 1
    return [
        {
            "period": period,
            "unique_applications": count,
            "by_account": dict(sorted(by_month_account[period].items())),
        }
        for period, count in sorted(by_month.items())
    ]


def _confirmation_daily(confirmations: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    daily: Counter[str] = Counter()
    for evidence in confirmations.values():
        daily[evidence["date"]] += 1
    return [
        {"date": occurred_on, "unique_applications": count}
        for occurred_on, count in sorted(daily.items())
    ]


def _confirmation_by_role(confirmations: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    roles: Counter[str] = Counter()
    for evidence in confirmations.values():
        role = evidence.get("role_family", "")
        if role:
            roles[role] += 1
    return [
        {"role_family": role, "confirmed_resume_submissions": count}
        for role, count in roles.most_common()
    ]


def _confirmation_monthly_by_role(
    confirmations: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    monthly: Counter[tuple[str, str]] = Counter()
    for evidence in confirmations.values():
        role = evidence.get("role_family", "")
        if role:
            monthly[(evidence["period"], role)] += 1
    return [
        {
            "period": period,
            "role_family": role,
            "confirmed_resume_submissions": count,
        }
        for (period, role), count in sorted(monthly.items(), reverse=True)
    ]


def _linkedin_extension_activity(database_path: Path) -> dict[str, Any]:
    """Return dated, first-class legacy browser-extension application evidence."""
    monthly: Counter[str] = Counter()
    daily: Counter[str] = Counter()
    with sqlite3.connect(database_path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
        required = {"linkedin_job_id", "source", "status", "first_seen_at", "applied_at"}
        if not required.issubset(columns):
            return {
                "submission_count": 0,
                "monthly": [],
                "daily": [],
                "fully_covered_periods": [],
                "coverage_start_date": None,
                "date_definition": (
                    "Legacy extension applied_at, populated from the original scan time."
                ),
            }
        rows = connection.execute(
            """
            SELECT linkedin_job_id, COALESCE(applied_at, first_seen_at) AS applied_at
              FROM jobs
             WHERE source = 'linkedin'
               AND status = 'applied'
               AND COALESCE(applied_at, first_seen_at) IS NOT NULL
            """
        ).fetchall()
    for _, applied_at in rows:
        value = str(applied_at or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value):
            continue
        monthly[value[:7]] += 1
        daily[value[:10]] += 1
    first_date = min(daily) if daily else None
    first_period = first_date[:7] if first_date else None
    fully_covered_periods = set(monthly)
    if first_date and first_period and not first_date.endswith("-01"):
        fully_covered_periods.discard(first_period)
    return {
        "submission_count": sum(monthly.values()),
        "monthly": [
            {"period": period, "submissions": count} for period, count in sorted(monthly.items())
        ],
        "daily": [{"date": value, "submissions": count} for value, count in sorted(daily.items())],
        "fully_covered_periods": sorted(fully_covered_periods),
        "coverage_start_date": first_date,
        "date_definition": "Legacy extension applied_at, populated from the original scan time.",
    }


def _combined_application_activity(
    plan: dict[str, Any], email: dict[str, Any], extension: dict[str, Any], through_date: date
) -> list[dict[str, Any]]:
    plan_months = {row["period"]: row for row in plan["monthly"]}
    email_months = {
        row["period"]: row for row in email.get("application_confirmations_monthly", [])
    }
    extension_months = {row["period"]: row for row in extension["monthly"]}
    fully_covered_extension_months = set(extension["fully_covered_periods"])
    first = date(2024, 7, 1)
    cursor = first.replace(day=1)
    end = through_date.replace(day=1)
    rows: list[dict[str, Any]] = []
    previous: int | None = None
    while cursor <= end:
        period = cursor.strftime("%Y-%m")
        plan_row = plan_months.get(period)
        email_row = email_months.get(period)
        extension_row = extension_months.get(period)
        plan_count = int(plan_row["applications"]) if plan_row else None
        email_count = int(email_row["unique_applications"]) if email_row else 0
        extension_count = int(extension_row["submissions"]) if extension_row else 0
        if extension_row is not None and period in fully_covered_extension_months:
            combined = extension_count
            source = "linkedin_extension_ledger"
        elif plan_count is not None:
            combined = plan_count
            source = "application_plan"
        else:
            combined = email_count
            source = "email_confirmation"
        rows.append(
            {
                "period": period,
                "plan_applications": plan_count,
                "linkedin_extension_applications": extension_count,
                "email_confirmed_applications": email_count,
                "combined_unique_applications": combined,
                "combined_source": source,
                "change_percent": _change_percent(combined, previous),
                "comparison_label": _prior_period(period) if previous is not None else None,
            }
        )
        previous = combined
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return rows


def _rolling_application_activity(
    plan: dict[str, Any], email: dict[str, Any], extension: dict[str, Any], through_date: date
) -> list[dict[str, Any]]:
    """Return source-aware 30/60/90-day totals without double-counting plan months."""
    plan_by_day = {row["date"]: int(row["applications"]) for row in plan["daily"]}
    plan_months = {row["period"] for row in plan["monthly"]}
    email_by_day = {
        row["date"]: int(row["unique_applications"])
        for row in email.get("application_confirmations_daily", [])
    }
    extension_by_day = {row["date"]: int(row["submissions"]) for row in extension["daily"]}
    extension_months = set(extension["fully_covered_periods"])
    result = []
    for days in (30, 60, 90):
        start = through_date - timedelta(days=days - 1)
        total = plan_days = extension_days = email_days = 0
        cursor = start
        while cursor <= through_date:
            day = cursor.isoformat()
            if cursor.strftime("%Y-%m") in extension_months:
                total += extension_by_day.get(day, 0)
                extension_days += 1
            elif cursor.strftime("%Y-%m") in plan_months:
                total += plan_by_day.get(day, 0)
                plan_days += 1
            else:
                total += email_by_day.get(day, 0)
                email_days += 1
            cursor += timedelta(days=1)
        result.append(
            {
                "days": days,
                "start_date": start.isoformat(),
                "end_date": through_date.isoformat(),
                "combined_unique_applications": total,
                "plan_days": plan_days,
                "linkedin_extension_days": extension_days,
                "email_confirmation_days": email_days,
            }
        )
    return result


def _monthly_intelligence(
    combined: list[dict[str, Any]],
    email: dict[str, Any],
    calendar: dict[str, Any],
) -> list[dict[str, Any]]:
    outcomes = {row["period"]: row for row in email.get("linked_outcomes_monthly", [])}
    calendar_months = {row["period"]: row for row in calendar["monthly"]}
    result = []
    for application_row in combined:
        period = application_row["period"]
        application_count = int(application_row["combined_unique_applications"])
        outcome = outcomes.get(period, {})
        replies = int(outcome.get("recruiter_replies", 0))
        interviews = int(outcome.get("interviews", 0))
        offers = int(outcome.get("offers", 0))
        result.append(
            {
                **application_row,
                "recruiter_replies": replies,
                "linked_interviews": interviews,
                "calendar_interview_events": int(calendar_months.get(period, {}).get("events", 0)),
                "offers": offers,
                "rejections": int(outcome.get("rejections", 0)),
                "response_rate": _rate(replies, application_count),
                "interview_conversion": _rate(interviews, application_count),
                "offer_conversion": _rate(offers, application_count),
            }
        )
    return result


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def _company_from_text(text: str, companies: list[str]) -> str:
    folded = text.casefold()
    for company in companies:
        if not _valid_company(company):
            continue
        normalized = company.casefold()
        if not _contains_company_name(folded, company):
            continue
        token = re.escape(normalized)
        if re.search(
            rf"(?:\b(?:at|with|from|for|join)\s+){token}(?!\w)|"
            rf"(?<!\w){token}.{{0,30}}\b(?:role|position|team|interview|application)\b",
            folded,
        ):
            return company
    return ""


def _valid_company(value: str) -> bool:
    cleaned = value.strip()
    return bool(
        len(cleaned) >= 3
        and len(cleaned.split()) <= 6
        and not GENERIC_COMPANY_PATTERN.search(cleaned)
        and cleaned.casefold() not in MEETING_PLATFORM_NAMES
        and cleaned.casefold() not in GENERIC_COMPANY_NAMES
        and not re.search(r"[^A-Za-z0-9 &'().+-]", cleaned)
        and re.search(r"[A-Za-z]", cleaned)
    )


def _counter_rows(groups: dict[str, Counter[str]], label: str) -> list[dict[str, Any]]:
    rows = []
    for name, counts in groups.items():
        rows.append({label: name, "events": sum(counts.values()), **dict(counts)})
    return sorted(rows, key=lambda row: (-row["events"], str(row[label]).casefold()))


def build_attributed_snapshot(
    plan_path: Path,
    funnel_path: Path,
    calendar_path: Path,
    database_path: Path,
    *,
    through_date: date,
    linkedin_submission_ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Create an aggregate-only snapshot; source message/event text is never emitted."""
    application_activity = _plan_activity(plan_path)
    email_evidence = _email_evidence(database_path)
    ledger_path = database_path
    extension_activity = _linkedin_extension_activity(database_path)
    if linkedin_submission_ledger_path and linkedin_submission_ledger_path != database_path:
        external_activity = _linkedin_extension_activity(linkedin_submission_ledger_path)
        if external_activity["submission_count"] > extension_activity["submission_count"]:
            ledger_path = linkedin_submission_ledger_path
            extension_activity = external_activity
    calendar = _calendar_attribution(calendar_path, database_path, through_date)
    application_activity["combined_monthly"] = _combined_application_activity(
        application_activity, email_evidence, extension_activity, through_date
    )
    application_activity["combined_unique_applications"] = sum(
        row["combined_unique_applications"] for row in application_activity["combined_monthly"]
    )
    application_activity["rolling_windows"] = _rolling_application_activity(
        application_activity, email_evidence, extension_activity, through_date
    )
    application_activity["intelligence_monthly"] = _monthly_intelligence(
        application_activity["combined_monthly"], email_evidence, calendar
    )
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "through_date": through_date.isoformat(),
        "sources": {
            "application_plan": {"filename": plan_path.name, "sha256": _sha256(plan_path)},
            "funnel_analysis": {"filename": funnel_path.name, "sha256": _sha256(funnel_path)},
            "calendar": {"filename": calendar_path.name, "sha256": _sha256(calendar_path)},
            "linkedin_extension_ledger": {
                "submission_count": extension_activity["submission_count"],
                "date_definition": extension_activity["date_definition"],
                "filename": ledger_path.name,
                "sha256": _sha256(ledger_path),
            },
        },
        "application_activity": application_activity,
        "funnel": _funnel(funnel_path),
        "calendar": calendar,
        "email_evidence": email_evidence,
        "limitations": [
            "The manual application log has gaps and ends before the snapshot date.",
            "Combined unique applications use the legacy LinkedIn submission ledger where it is "
            "available, then the application plan, then deduplicated email confirmations.",
            "Email-confirmation fallback is a conservative floor until each provider checkpoint "
            "reaches the end of its folder.",
            "Account and role funnel values reproduce the supplied analysis document.",
            "Calendar events are interview rounds, not unique application conversions.",
            "Calendar company and role matches are deterministic but require review.",
        ],
    }


def load_attributed_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError("unsupported attributed analytics snapshot")
    return data
