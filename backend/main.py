
from __future__ import annotations

import csv
import html
import io
import json
import mailbox
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

try:
    from backend.app.database.paths import (
        initialize_database_if_missing,
        resolve_database_path,
    )
    from backend.app.services.email_classification import (
        ClassificationResult,
        EmailType,
        classify_email,
    )
    from backend.app.services.analytics import (
        analytics_companies as corrected_analytics_companies,
        analytics_overview as corrected_analytics_overview,
        analytics_roles as corrected_analytics_roles,
        analytics_timeline as corrected_analytics_timeline,
    )
    from backend.app.services.attributed_analytics import load_attributed_snapshot
    from backend.app.services.application_attribution import infer_application_role_family
    from backend.app.services.evidence_review import list_unlinked_evidence
    from backend.app.services.import_identity import stable_message_identity
    from backend.app.services.historical_interview_import import (
        HistoricalInterviewCandidate,
        HistoricalMessage,
        build_interview_candidate,
    )
    from backend.app.services.interview_pipeline import (
        InterviewEvidence,
        extract_interview,
    )
    from backend.app.services.recruiter_crm import (
        RecruiterEvidence,
        extract_recruiter,
        normalize_company as normalize_recruiter_company,
    )
    from backend.app.services.sync_status import provider_sync_status
    from backend.app.services.version1_product import (
        company_timeline,
        create_application,
        create_company,
        create_interaction,
        create_job_description,
        create_note,
        create_offer,
        create_resume,
        get_application,
        get_recruiter_relationship,
        list_applications,
        list_companies,
        list_job_descriptions,
        list_notes,
        list_offers,
        list_resumes,
        schema_ready,
        update_application,
        update_offer,
        upsert_recruiter_relationship,
        version1_analytics,
    )
    from backend.app.services.yahoo_imap import YahooImapMessage
    from backend.app.schemas.version1 import (
        ApplicationInput,
        ApplicationUpdate,
        CompanyInput,
        InteractionInput,
        JobDescriptionInput,
        NoteInput,
        OfferInput,
        OfferUpdate,
        RecruiterRelationshipInput,
        ResumeInput,
    )
except ModuleNotFoundError:  # Supports the existing `cd backend && uvicorn main:app` command.
    from app.database.paths import initialize_database_if_missing, resolve_database_path
    from app.services.email_classification import ClassificationResult, EmailType, classify_email
    from app.services.analytics import (
        analytics_companies as corrected_analytics_companies,
        analytics_overview as corrected_analytics_overview,
        analytics_roles as corrected_analytics_roles,
        analytics_timeline as corrected_analytics_timeline,
    )
    from app.services.attributed_analytics import load_attributed_snapshot
    from app.services.application_attribution import infer_application_role_family
    from app.services.evidence_review import list_unlinked_evidence
    from app.services.import_identity import stable_message_identity
    from app.services.historical_interview_import import (
        HistoricalInterviewCandidate,
        HistoricalMessage,
        build_interview_candidate,
    )
    from app.services.interview_pipeline import InterviewEvidence, extract_interview
    from app.services.recruiter_crm import (
        RecruiterEvidence,
        extract_recruiter,
        normalize_company as normalize_recruiter_company,
    )
    from app.services.sync_status import provider_sync_status
    from app.services.version1_product import (
        company_timeline,
        create_application,
        create_company,
        create_interaction,
        create_job_description,
        create_note,
        create_offer,
        create_resume,
        get_application,
        get_recruiter_relationship,
        list_applications,
        list_companies,
        list_job_descriptions,
        list_notes,
        list_offers,
        list_resumes,
        schema_ready,
        update_application,
        update_offer,
        upsert_recruiter_relationship,
        version1_analytics,
    )
    from app.services.yahoo_imap import YahooImapMessage
    from app.schemas.version1 import (
        ApplicationInput,
        ApplicationUpdate,
        CompanyInput,
        InteractionInput,
        JobDescriptionInput,
        NoteInput,
        OfferInput,
        OfferUpdate,
        RecruiterRelationshipInput,
        ResumeInput,
    )

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = resolve_database_path()
initialize_database_if_missing(DB_PATH)
ATTRIBUTED_ANALYTICS_PATH = Path(
    os.environ.get("ATTRIBUTED_ANALYTICS_PATH", DB_PATH.parent / "attributed_analytics.json")
).expanduser()

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

class Base(DeclarativeBase):
    pass


class ProvenanceBase(DeclarativeBase):
    """Models managed only by Alembic, never by legacy create_all()."""

    pass

class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    linkedin_job_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    salary_text: Mapped[str] = mapped_column(String(300), default="")
    applicant_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    applicant_count_is_over: Mapped[bool] = mapped_column(Boolean, default=False)
    applicant_text: Mapped[str] = mapped_column(String(300), default="")
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    posted_text: Mapped[str] = mapped_column(String(200), default="")
    work_mode: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(50), default="linkedin")
    status: Mapped[str] = mapped_column(String(50), default="new")
    notes: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    email_account: Mapped[str] = mapped_column(String(50), default="")
    role_family: Mapped[str] = mapped_column(String(100), default="")
    resume_family: Mapped[str] = mapped_column(String(100), default="")
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmation_message_id: Mapped[str] = mapped_column(String(500), default="")
    ats_platform: Mapped[str] = mapped_column(String(100), default="")
    requisition_id: Mapped[str] = mapped_column(String(200), default="")
    application_source: Mapped[str] = mapped_column(String(100), default="")
    import_confidence: Mapped[float] = mapped_column(Float, default=0.0)

class EmailImport(Base):
    __tablename__ = "email_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mailbox_name: Mapped[str] = mapped_column(String(50), default="")
    source_filename: Mapped[str] = mapped_column(String(500), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    confirmations_found: Mapped[int] = mapped_column(Integer, default=0)
    matched_jobs: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_jobs: Mapped[int] = mapped_column(Integer, default=0)


class ImportedMessage(ProvenanceBase):
    __tablename__ = "imported_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    source_import_id: Mapped[int] = mapped_column(Integer)
    stable_message_identity: Mapped[str] = mapped_column(String(67), unique=True)
    original_message_id: Mapped[str] = mapped_column(String(500), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))
    error: Mapped[str] = mapped_column(Text, default="")


class EmailClassification(ProvenanceBase):
    __tablename__ = "email_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_identity: Mapped[str] = mapped_column(String(67))
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    classification: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[float] = mapped_column(Float)
    classifier_version: Mapped[str] = mapped_column(String(50))
    reason_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Recruiter(ProvenanceBase):
    __tablename__ = "recruiters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    linkedin_url: Mapped[str] = mapped_column(String(1000), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RecruiterCompanyLink(ProvenanceBase):
    __tablename__ = "recruiter_company_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recruiter_id: Mapped[int] = mapped_column(Integer)
    company_name: Mapped[str] = mapped_column(String(300))
    normalized_company_name: Mapped[str] = mapped_column(String(300))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RecruiterEmailAddress(ProvenanceBase):
    __tablename__ = "recruiter_email_addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recruiter_id: Mapped[int] = mapped_column(Integer)
    email: Mapped[str] = mapped_column(String(500))
    normalized_email: Mapped[str] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RecruiterJobLink(ProvenanceBase):
    __tablename__ = "recruiter_job_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recruiter_id: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[int] = mapped_column(Integer)
    source_message_identity: Mapped[str] = mapped_column(String(67))
    relationship_type: Mapped[str] = mapped_column(String(50))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Interview(ProvenanceBase):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer)
    recruiter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    interview_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    scheduled_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    location_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    location_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meeting_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    first_source_message_identity: Mapped[str] = mapped_column(String(67))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class InterviewEvent(ProvenanceBase):
    __tablename__ = "interview_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recruiter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_message_identity: Mapped[str] = mapped_column(String(67))
    classification_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(50))
    event_type: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extracted_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    location_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    location_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meeting_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text)
    extractor_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ImapSyncCheckpoint(ProvenanceBase):
    __tablename__ = "imap_sync_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50))
    account_namespace: Mapped[str] = mapped_column(String(500))
    folder: Mapped[str] = mapped_column(String(1000))
    since_date: Mapped[date] = mapped_column(Date)
    uidvalidity: Mapped[str] = mapped_column(String(100))
    last_successful_uid: Mapped[int] = mapped_column(Integer)
    sync_started_at: Mapped[datetime] = mapped_column(DateTime)
    sync_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scanned_count: Mapped[int] = mapped_column(Integer)
    accepted_count: Mapped[int] = mapped_column(Integer)
    skipped_count: Mapped[int] = mapped_column(Integer)
    failure_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class ImapMessageMetadata(ProvenanceBase):
    __tablename__ = "imap_message_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_identity: Mapped[str] = mapped_column(String(67))
    provider: Mapped[str] = mapped_column(String(50))
    account_namespace: Mapped[str] = mapped_column(String(500))
    folder: Mapped[str] = mapped_column(String(1000))
    uidvalidity: Mapped[str] = mapped_column(String(100))
    imap_uid: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(Text)
    sender: Mapped[str] = mapped_column(Text)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    imap_internal_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    requested_since_date: Mapped[date] = mapped_column(Date)
    text_body: Mapped[str] = mapped_column(Text)
    html_fallback_used: Mapped[bool] = mapped_column(Boolean)
    recipients_json: Mapped[str] = mapped_column(Text)
    attachments_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)

Base.metadata.create_all(engine)

def migrate_database() -> None:
    """Add v2 columns to an existing v1 SQLite database without deleting data."""
    required = {
        "email_account": "TEXT DEFAULT ''",
        "role_family": "TEXT DEFAULT ''",
        "resume_family": "TEXT DEFAULT ''",
        "applied_at": "DATETIME",
        "confirmation_message_id": "TEXT DEFAULT ''",
        "ats_platform": "TEXT DEFAULT ''",
        "requisition_id": "TEXT DEFAULT ''",
        "application_source": "TEXT DEFAULT ''",
        "import_confidence": "REAL DEFAULT 0.0",
    }

    with sqlite3.connect(DB_PATH) as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column, ddl in required.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
        conn.commit()

migrate_database()

class JobIn(BaseModel):
    linkedin_job_id: str = Field(min_length=1)
    title: str = ""
    company: str = ""
    location: str = ""
    salary_text: str = ""
    applicant_count: Optional[int] = None
    applicant_count_is_over: bool = False
    applicant_text: str = ""
    easy_apply: bool = False
    promoted: bool = False
    posted_text: str = ""
    work_mode: str = ""
    description: str = ""
    url: str = ""
    source: str = "linkedin"

class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None

class YahooImportItem(BaseModel):
    company: str = ""
    title: str = ""
    applied_at: Optional[datetime] = None
    job_url: str = ""
    job_id: str = ""
    requisition_id: str = ""
    ats_platform: str = ""
    confirmation_message_id: str = ""
    subject: str = ""
    sender: str = ""
    body: str = ""

class YahooImportPayload(BaseModel):
    records: list[YahooImportItem]

app = FastAPI(title="Job Intelligence v2", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ACCOUNT_MAP = {
    "yahoo": {
        "role_family": "Product Manager / Technical Program Manager",
        "resume_family": "Product / TPM",
    },
    "hotmail": {
        "role_family": "Marketing",
        "resume_family": "Growth / Lifecycle / Product Marketing",
    },
    "gmail": {
        "role_family": "Sales Engineer / Delivery Manager",
        "resume_family": "Sales Engineering / Delivery",
    },
}

ACCOUNT_NAMESPACE_MAP = {
    ("gmail", "solovat@gmail.com"): {
        "role_family": "Product Manager / Technical Program Manager",
        "resume_family": "Product / TPM",
    },
    ("gmail", "soultanovr@gmail.com"): {
        "role_family": "Marketing",
        "resume_family": "Growth / Lifecycle / Product Marketing",
    },
    ("gmail", "ibuildanapp@gmail.com"): {
        "role_family": "Operations / Sales Engineering",
        "resume_family": "Operations / Sales Engineering",
    },
}


def account_profile(mailbox_name: str, account_namespace: str = "") -> dict[str, str]:
    """Return the explicit account profile without collapsing same-provider mailboxes."""
    normalized_account = account_namespace.strip().casefold()
    return ACCOUNT_NAMESPACE_MAP.get(
        (mailbox_name, normalized_account), ACCOUNT_MAP[mailbox_name]
    )

ATS_DOMAINS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "myworkdayjobs.com": "Workday",
    "ashbyhq.com": "Ashby",
    "smartrecruiters.com": "SmartRecruiters",
    "icims.com": "iCIMS",
    "jobvite.com": "Jobvite",
    "linkedin.com": "LinkedIn",
}

CONFIRMATION_PATTERNS = [
    r"thank you for applying",
    r"application (?:has been )?received",
    r"we received your application",
    r"application confirmation",
    r"thanks for your interest",
    r"successfully applied",
    r"your application to",
    r"submitted your application",
]

TITLE_PATTERNS = [
    re.compile(r"(?:position|role|job)\s*[:\-]\s*([^\n]{3,180})", re.I),
    re.compile(
        r"(?:for|to)\s+(?:the\s+)?(?:position|role|job)(?:\s+of)?\s+([^\n]{3,180})",
        re.I,
    ),
    re.compile(r"application\s+(?:for|to)\s+([^\n]{3,180})", re.I),
]

def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value

def message_body(message: Message) -> str:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition.lower():
                continue
            if content_type in {"text/plain", "text/html"}:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    if payload:
                        parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    continue
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"
            if payload:
                parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass

    text = html.unescape("\n".join(parts))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_email_date(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    from email.utils import parsedate_to_datetime
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None

def infer_ats(text: str) -> str:
    lower = text.lower()
    for domain, name in ATS_DOMAINS.items():
        if domain in lower:
            return name
    return ""

def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s<>\"]+", text)

def extract_job_id(text: str) -> str:
    match = re.search(r"linkedin\.com/jobs/view/(\d+)", text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"(?:job|requisition|req)\s*(?:id|#)?\s*[:#-]?\s*([A-Z0-9_-]{4,30})", text, re.I)
    return match.group(1) if match else ""

def extract_requisition_id(text: str) -> str:
    patterns = [
        r"requisition\s*(?:id|#)?\s*[:#-]?\s*([A-Z0-9_-]{4,30})",
        r"req\s*(?:id|#)?\s*[:#-]?\s*([A-Z0-9_-]{4,30})",
        r"job\s*id\s*[:#-]?\s*([A-Z0-9_-]{4,30})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return ""

def normalize_company(value: str) -> str:
    value = re.sub(r"\b(inc\.?|llc|corp\.?|corporation|company|ltd\.?)\b", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

def normalize_title(value: str) -> str:
    value = re.sub(r"\b(sr\.?|senior|lead|principal|staff|manager|director|head of)\b", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

def clean_extracted_title(value: str) -> str:
    """Keep a short role phrase; never turn surrounding email prose into a title."""
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip(" .:-")
    value = re.sub(r"^(?:the\s+)?(?:position|role|job)(?:\s+of)?\s+", "", value, flags=re.I)
    boundary = re.search(
        r"(?:\.|\s+(?:and|at|with|where|while|if|we|our|your|you)\b|\s+-\s+(?:we|our)\b)",
        value,
        re.I,
    )
    if boundary:
        value = value[: boundary.start()]
    value = value.strip(" ,.:;|–—-()")
    if len(value) > 100 or len(value.split()) > 12:
        return ""
    if value.casefold() in {"application", "application confirmation", "your application"}:
        return ""
    return value


def extract_company_and_title(subject: str, body: str, sender: str) -> tuple[str, str]:
    combined = f"{subject} {body}"

    title = ""
    for pattern in TITLE_PATTERNS:
        match = pattern.search(combined)
        if match:
            title = clean_extracted_title(match.group(1))
            if title:
                break

    company = ""
    domain_match = re.search(r"@([a-z0-9.-]+)", sender.lower())
    if domain_match:
        domain = domain_match.group(1).split(".")[0]
        if domain not in {"linkedin", "greenhouse", "lever", "workday", "indeed", "smartrecruiters", "ashbyhq"}:
            company = domain.replace("-", " ").title()

    subject_patterns = [
        r"(?:application|interest)\s+(?:to|at|with)\s+([A-Z][A-Za-z0-9& .'-]{2,80})",
        r"([A-Z][A-Za-z0-9& .'-]{2,80})\s+(?:application|careers)",
    ]
    for pattern in subject_patterns:
        match = re.search(pattern, subject)
        if match:
            company = match.group(1).strip(" .:-")
            break

    return company, title

def is_confirmation(subject: str, body: str) -> bool:
    text = f"{subject} {body}".lower()
    return any(re.search(pattern, text, re.I) for pattern in CONFIRMATION_PATTERNS)

def compute_score(job: JobIn) -> float:
    score = 50.0
    if job.applicant_count is not None:
        if job.applicant_count_is_over:
            score -= 35
        elif job.applicant_count < 25:
            score += 35
        elif job.applicant_count < 50:
            score += 28
        elif job.applicant_count < 100:
            score += 18
        elif job.applicant_count < 200:
            score -= 5
        else:
            score -= 25

    text = f"{job.title} {job.description}".lower()
    if any(k in text for k in ["growth", "lifecycle", "product marketing", "performance marketing"]):
        score += 8
    if any(k in text for k in ["senior", "lead", "principal", "manager", "director"]):
        score += 4
    if job.easy_apply:
        score += 2
    if job.promoted:
        score -= 3
    if "remote" in (job.location + " " + job.work_mode).lower():
        score += 2

    return max(0.0, min(100.0, round(score, 1)))

def serialize(job: Job) -> dict:
    return {
        "id": job.id,
        "linkedin_job_id": job.linkedin_job_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "salary_text": job.salary_text,
        "applicant_count": job.applicant_count,
        "applicant_count_is_over": job.applicant_count_is_over,
        "applicant_text": job.applicant_text,
        "easy_apply": job.easy_apply,
        "promoted": job.promoted,
        "posted_text": job.posted_text,
        "work_mode": job.work_mode,
        "description": job.description,
        "url": job.url,
        "source": job.source,
        "status": job.status,
        "notes": job.notes,
        "score": job.score,
        "first_seen_at": job.first_seen_at.isoformat() if job.first_seen_at else None,
        "last_seen_at": job.last_seen_at.isoformat() if job.last_seen_at else None,
        "email_account": job.email_account,
        "role_family": job.role_family,
        "resume_family": job.resume_family,
        "applied_at": job.applied_at.isoformat() if job.applied_at else None,
        "confirmation_message_id": job.confirmation_message_id,
        "ats_platform": job.ats_platform,
        "requisition_id": job.requisition_id,
        "application_source": job.application_source,
        "import_confidence": job.import_confidence,
    }

def match_or_create_application(
    session: Session,
    *,
    mailbox_name: str,
    company: str,
    title: str,
    applied_at: Optional[datetime],
    job_url: str,
    job_id: str,
    requisition_id: str,
    ats_platform: str,
    message_id: str,
    stable_identity: str,
    account_namespace: str = "",
    inferred_role_family: str = "",
) -> tuple[Job, float, bool]:
    account_key = account_namespace.strip().casefold() or mailbox_name
    account = account_profile(mailbox_name, account_key)
    jobs = list(session.scalars(select(Job)))

    best_job: Optional[Job] = None
    best_score = 0.0

    for job in jobs:
        if job.email_account and job.email_account != account_key:
            continue
        score = 0.0
        if job_id and job.linkedin_job_id == job_id:
            score += 100
        if requisition_id and job.requisition_id and job.requisition_id.lower() == requisition_id.lower():
            score += 70

        company_a = normalize_company(company)
        company_b = normalize_company(job.company)
        if company_a and company_b:
            if company_a == company_b:
                score += 35
            elif company_a in company_b or company_b in company_a:
                score += 22

        title_a = normalize_title(title)
        title_b = normalize_title(job.title)
        if title_a and title_b:
            if title_a == title_b:
                score += 35
            else:
                tokens_a = set(title_a.split())
                tokens_b = set(title_b.split())
                if tokens_a and tokens_b:
                    overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
                    score += overlap * 30

        if applied_at and job.first_seen_at:
            delta_days = abs((applied_at - job.first_seen_at).days)
            if delta_days <= 3:
                score += 20
            elif delta_days <= 14:
                score += 10
            elif delta_days <= 45:
                score += 4

        if score > best_score:
            best_score = score
            best_job = job

    role_conflict = bool(
        best_job
        and inferred_role_family
        and best_job.role_family
        and best_job.role_family != inferred_role_family
        and not job_id
        and not requisition_id
    )
    matched = bool(best_job and best_score >= 45 and not role_conflict)

    created = not matched
    if created:
        identifier_owner = (
            session.scalar(select(Job).where(Job.linkedin_job_id == job_id)) if job_id else None
        )
        reusable_identifier = bool(
            job_id
            and (
                identifier_owner is None
                or not identifier_owner.email_account
                or identifier_owner.email_account == account_key
            )
        )
        synthetic_id = (
            job_id
            if reusable_identifier
            else f"email-{account_key}-{stable_identity.removeprefix('v1:')[:32]}"
        )
        best_job = Job(
            linkedin_job_id=synthetic_id,
            title=title or "Application confirmation",
            company=company,
            url=job_url,
            source="email",
            status="applied",
            score=50.0,
            first_seen_at=applied_at or datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        session.add(best_job)

    if not best_job.email_account:
        best_job.email_account = account_key
    if not best_job.role_family:
        best_job.role_family = inferred_role_family or account["role_family"]
    if not best_job.resume_family:
        best_job.resume_family = account["resume_family"]
    if applied_at and best_job.applied_at is None:
        best_job.applied_at = applied_at
    if message_id and not best_job.confirmation_message_id:
        best_job.confirmation_message_id = message_id
    if ats_platform and not best_job.ats_platform:
        best_job.ats_platform = ats_platform
    if requisition_id and not best_job.requisition_id:
        best_job.requisition_id = requisition_id
    if not best_job.application_source:
        best_job.application_source = "email_confirmation"
    if created:
        best_job.status = "applied"
    if matched:
        best_job.import_confidence = max(
            best_job.import_confidence,
            min(100.0, round(best_score, 1)),
        )

    if job_url and not best_job.url:
        best_job.url = job_url
    if company and not best_job.company:
        best_job.company = company
    if title and (not best_job.title or best_job.title == "Application confirmation"):
        best_job.title = title

    return best_job, best_score, matched


def imported_message_exists(session: Session, identity: str) -> bool:
    return session.scalar(
        select(ImportedMessage.id).where(
            ImportedMessage.stable_message_identity == identity
        )
    ) is not None


def record_imported_message(
    session: Session,
    *,
    provider: str,
    source_import_id: int,
    identity: str,
    original_message_id: str,
    job_id: Optional[int],
    outcome: str,
    error: str = "",
) -> ImportedMessage:
    message = ImportedMessage(
        provider=provider,
        source_import_id=source_import_id,
        stable_message_identity=identity,
        original_message_id=original_message_id,
        job_id=job_id,
        outcome=outcome,
        error=error,
    )
    session.add(message)
    return message


def record_email_classification(
    session: Session,
    *,
    identity: str,
    job_id: Optional[int],
    result: ClassificationResult,
) -> EmailClassification:
    record = EmailClassification(
        message_identity=identity,
        job_id=job_id,
        classification=result.classification.value,
        confidence=result.confidence,
        classifier_version=result.classifier_version,
        reason_json=json.dumps(list(result.reasons), separators=(",", ":")),
    )
    session.add(record)
    return record


def find_email_classification(
    session: Session, identity: str, classifier_version: str
) -> Optional[EmailClassification]:
    return session.scalar(
        select(EmailClassification).where(
            EmailClassification.message_identity == identity,
            EmailClassification.classifier_version == classifier_version,
        )
    )


def find_explicit_job(
    session: Session, content: str, provider: Optional[str] = None
) -> Optional[Job]:
    """Resolve a job only from an explicit job or requisition identifier."""
    identifiers = {extract_job_id(content), extract_requisition_id(content)} - {""}
    for identifier in identifiers:
        job = session.scalar(
            select(Job).where(
                (Job.linkedin_job_id == identifier) | (Job.requisition_id == identifier)
            )
        )
        if job and (not provider or not job.email_account or job.email_account == provider):
            return job
    return None


def find_recruiter(session: Session, evidence: RecruiterEvidence) -> Optional[Recruiter]:
    company_filter = (
        RecruiterCompanyLink.normalized_company_name == evidence.normalized_company
    )
    recruiter = session.scalar(
        select(Recruiter)
        .join(RecruiterEmailAddress, RecruiterEmailAddress.recruiter_id == Recruiter.id)
        .join(RecruiterCompanyLink, RecruiterCompanyLink.recruiter_id == Recruiter.id)
        .where(
            RecruiterEmailAddress.normalized_email == evidence.normalized_email,
            company_filter,
        )
    )
    if recruiter:
        return recruiter
    if evidence.name:
        return session.scalar(
            select(Recruiter)
            .join(RecruiterCompanyLink, RecruiterCompanyLink.recruiter_id == Recruiter.id)
            .where(Recruiter.name == evidence.name, company_filter)
        )
    if not evidence.signature:
        return None
    return session.scalar(
        select(Recruiter)
        .join(RecruiterCompanyLink, RecruiterCompanyLink.recruiter_id == Recruiter.id)
        .where(Recruiter.signature == evidence.signature, company_filter)
    )


def record_recruiter(
    session: Session,
    *,
    evidence: RecruiterEvidence,
    identity: str,
    job: Optional[Job],
    observed_at: datetime,
) -> Recruiter:
    recruiter = find_recruiter(session, evidence)
    if recruiter is None:
        recruiter = Recruiter(
            name=evidence.name,
            title=evidence.title,
            signature=evidence.signature,
            linkedin_url=evidence.linkedin_url,
            phone=evidence.phone,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            created_at=observed_at,
            updated_at=observed_at,
        )
        session.add(recruiter)
        session.flush()
        _add_recruiter_contacts(session, recruiter.id, evidence, observed_at)
    else:
        recruiter.last_seen_at = max(recruiter.last_seen_at, observed_at)
        recruiter.updated_at = observed_at
        _preserve_recruiter_fields(recruiter, evidence)
        _touch_recruiter_contacts(session, recruiter.id, evidence, observed_at)
    if job:
        _record_recruiter_job_link(session, recruiter.id, job.id, identity, evidence, observed_at)
    return recruiter


def record_message_recruiter(
    session: Session,
    *,
    evidence: RecruiterEvidence,
    identity: str,
    job: Optional[Job],
    observed_at: datetime,
) -> Optional[Recruiter]:
    """Record deterministic contact evidence without linking conflicting companies."""
    compatible = (
        not job
        or not job.company
        or evidence.normalized_company == normalize_recruiter_company(job.company)
    )
    recruiter = record_recruiter(
        session,
        evidence=evidence,
        identity=identity,
        job=job if compatible else None,
        observed_at=observed_at,
    )
    return recruiter if compatible or job is None else None


def _add_recruiter_contacts(
    session: Session, recruiter_id: int, evidence: RecruiterEvidence, observed_at: datetime
) -> None:
    common = {
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        "created_at": observed_at,
        "updated_at": observed_at,
    }
    session.add(
        RecruiterCompanyLink(
            recruiter_id=recruiter_id,
            company_name=evidence.company,
            normalized_company_name=evidence.normalized_company,
            **common,
        )
    )
    session.add(
        RecruiterEmailAddress(
            recruiter_id=recruiter_id,
            email=evidence.email,
            normalized_email=evidence.normalized_email,
            **common,
        )
    )


def _touch_recruiter_contacts(
    session: Session, recruiter_id: int, evidence: RecruiterEvidence, observed_at: datetime
) -> None:
    company = session.scalar(
        select(RecruiterCompanyLink).where(
            RecruiterCompanyLink.recruiter_id == recruiter_id,
            RecruiterCompanyLink.normalized_company_name == evidence.normalized_company,
        )
    )
    email = session.scalar(
        select(RecruiterEmailAddress).where(
            RecruiterEmailAddress.recruiter_id == recruiter_id,
            RecruiterEmailAddress.normalized_email == evidence.normalized_email,
        )
    )
    if company:
        company.last_seen_at = max(company.last_seen_at, observed_at)
        company.updated_at = observed_at
    if email:
        email.last_seen_at = max(email.last_seen_at, observed_at)
        email.updated_at = observed_at
    else:
        session.add(
            RecruiterEmailAddress(
                recruiter_id=recruiter_id,
                email=evidence.email,
                normalized_email=evidence.normalized_email,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                created_at=observed_at,
                updated_at=observed_at,
            )
        )


def _preserve_recruiter_fields(recruiter: Recruiter, evidence: RecruiterEvidence) -> None:
    for field in ("name", "title", "signature", "linkedin_url", "phone"):
        if not getattr(recruiter, field) and getattr(evidence, field):
            setattr(recruiter, field, getattr(evidence, field))


def _record_recruiter_job_link(
    session: Session,
    recruiter_id: int,
    job_id: int,
    identity: str,
    evidence: RecruiterEvidence,
    observed_at: datetime,
) -> None:
    link = session.scalar(
        select(RecruiterJobLink).where(
            RecruiterJobLink.recruiter_id == recruiter_id,
            RecruiterJobLink.job_id == job_id,
            RecruiterJobLink.relationship_type == evidence.relationship_type,
        )
    )
    if link:
        link.last_seen_at = max(link.last_seen_at, observed_at)
        link.updated_at = observed_at
        return
    session.add(
        RecruiterJobLink(
            recruiter_id=recruiter_id,
            job_id=job_id,
            source_message_identity=identity,
            relationship_type=evidence.relationship_type,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            created_at=observed_at,
            updated_at=observed_at,
        )
    )


def find_message_recruiter(
    session: Session, sender: str, job: Optional[Job]
) -> Optional[Recruiter]:
    normalized_email = parseaddr(sender)[1].strip().casefold()
    if not normalized_email:
        return None
    query = (
        select(Recruiter)
        .join(RecruiterEmailAddress, RecruiterEmailAddress.recruiter_id == Recruiter.id)
        .where(RecruiterEmailAddress.normalized_email == normalized_email)
    )
    if job:
        query = query.join(
            RecruiterJobLink, RecruiterJobLink.recruiter_id == Recruiter.id
        ).where(RecruiterJobLink.job_id == job.id)
    matches = list(session.scalars(query))
    unique_matches = {item.id: item for item in matches}
    return next(iter(unique_matches.values())) if len(unique_matches) == 1 else None


def interview_event_exists(session: Session, identity: str, extractor_version: str) -> bool:
    return session.scalar(
        select(InterviewEvent.id).where(
            InterviewEvent.source_message_identity == identity,
            InterviewEvent.extractor_version == extractor_version,
        )
    ) is not None


def find_interview(
    session: Session,
    *,
    job: Job,
    evidence: InterviewEvidence,
) -> Optional[Interview]:
    candidates = list(session.scalars(select(Interview).where(Interview.job_id == job.id)))
    if evidence.event_identifier:
        events = list(
            session.scalars(select(InterviewEvent).where(InterviewEvent.job_id == job.id))
        )
        for event in events:
            parsed = json.loads(event.evidence_json).get("parsed_values", {})
            if parsed.get("event_identifier") == evidence.event_identifier:
                return session.get(Interview, event.interview_id) if event.interview_id else None
    if evidence.meeting_url:
        match = next((item for item in candidates if item.meeting_url == evidence.meeting_url), None)
        if match:
            return match
    if evidence.scheduled_start:
        match = next(
            (item for item in candidates if item.scheduled_start == evidence.scheduled_start), None
        )
        if match:
            return match
    return None


def record_interview_evidence(
    session: Session,
    *,
    evidence: InterviewEvidence,
    identity: str,
    classification_id: int,
    provider: str,
    job: Optional[Job],
    recruiter: Optional[Recruiter],
    observed_at: datetime,
) -> Optional[Interview]:
    interview = (
        find_interview(session, job=job, evidence=evidence) if job else None
    )
    if job and interview is None:
        interview = _new_interview(job, recruiter, evidence, identity, observed_at)
        session.add(interview)
        session.flush()
    elif interview:
        _update_interview(interview, recruiter, evidence, observed_at)
    event = InterviewEvent(
        interview_id=interview.id if interview else None,
        job_id=job.id if job else None,
        recruiter_id=recruiter.id if recruiter else None,
        source_message_identity=identity,
        classification_id=classification_id,
        provider=provider,
        event_type=evidence.event_type,
        occurred_at=observed_at,
        extracted_start=evidence.scheduled_start,
        extracted_end=evidence.scheduled_end,
        timezone=evidence.timezone_text or None,
        location_type=evidence.location_type,
        location_text=evidence.location_text or evidence.phone or None,
        meeting_url=evidence.meeting_url or None,
        evidence_json=_interview_evidence_json(evidence, linked=bool(interview)),
        extractor_version=evidence.extractor_version,
        created_at=datetime.utcnow(),
    )
    session.add(event)
    return interview


def _new_interview(
    job: Job,
    recruiter: Optional[Recruiter],
    evidence: InterviewEvidence,
    identity: str,
    observed_at: datetime,
) -> Interview:
    return Interview(
        job_id=job.id,
        recruiter_id=recruiter.id if recruiter else None,
        interview_type=evidence.interview_type,
        status=_interview_status(evidence.event_type),
        scheduled_start=evidence.scheduled_start,
        scheduled_end=evidence.scheduled_end,
        timezone=evidence.timezone_text or None,
        location_type=evidence.location_type,
        location_text=evidence.location_text or evidence.phone or None,
        meeting_url=evidence.meeting_url or None,
        title=evidence.title or None,
        first_source_message_identity=identity,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
    )


def _update_interview(
    interview: Interview,
    recruiter: Optional[Recruiter],
    evidence: InterviewEvidence,
    observed_at: datetime,
) -> None:
    interview.last_seen_at = max(interview.last_seen_at, observed_at)
    interview.updated_at = observed_at
    interview.status = _interview_status(evidence.event_type)
    if recruiter and interview.recruiter_id is None:
        interview.recruiter_id = recruiter.id
    for field in ("scheduled_start", "scheduled_end"):
        if getattr(evidence, field) is not None:
            setattr(interview, field, getattr(evidence, field))
    for field, value in (
        ("timezone", evidence.timezone_text),
        ("location_type", evidence.location_type),
        ("location_text", evidence.location_text or evidence.phone),
        ("meeting_url", evidence.meeting_url),
        ("title", evidence.title),
    ):
        if value:
            setattr(interview, field, value)


def _interview_status(event_type: str) -> str:
    return {
        "confirmation": "confirmed",
        "reschedule": "rescheduled",
        "cancellation": "cancelled",
    }.get(event_type, "scheduled")


def _interview_evidence_json(evidence: InterviewEvidence, *, linked: bool) -> str:
    payload = {
        "matched_signals": list(evidence.matched_signals),
        "parsed_values": {
            "local_start": evidence.local_start_text,
            "local_end": evidence.local_end_text,
            "timezone": evidence.timezone_text,
            "event_identifier": evidence.event_identifier,
            "job_identifier": evidence.job_identifier,
            "interview_type": evidence.interview_type,
        },
        "ambiguity_or_missing": list(evidence.ambiguity_reasons)
        + ([] if linked else ["no deterministic job linkage"]),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def import_historical_interview_messages(
    messages: Iterable[HistoricalMessage], *, source_name: str
) -> dict[str, int | str]:
    """Replay one historical provider export without invoking legacy job import behavior."""
    summary = _historical_summary()
    source_import: Optional[EmailImport] = None
    provider = ""
    with Session(engine) as session:
        for message in messages:
            _increment(summary, "total_messages")
            if provider and message.provider != provider:
                raise ValueError("Historical import batches must contain exactly one provider")
            provider = message.provider
            summary["provider"] = provider
            candidate = build_interview_candidate(message)
            if candidate is None:
                _increment(summary, "ignored_messages")
                continue
            _increment(summary, "deterministic_candidates")
            if interview_event_exists(
                session, candidate.identity, candidate.evidence.extractor_version
            ):
                _increment(summary, "already_recorded")
                continue
            source_import, linked, provenance_created, classification_created = (
                _record_historical_candidate(session, candidate, source_import, source_name)
            )
            _increment(summary, "inserted_events")
            _increment(summary, "linked_events" if linked else "unmatched_events")
            if provenance_created:
                _increment(summary, "created_provenance")
            if classification_created:
                _increment(summary, "created_classifications")
        _finish_historical_import(source_import, summary)
        session.commit()
    return summary


def _historical_summary() -> dict[str, int | str]:
    return {
        "provider": "",
        "total_messages": 0,
        "deterministic_candidates": 0,
        "ignored_messages": 0,
        "inserted_events": 0,
        "already_recorded": 0,
        "linked_events": 0,
        "unmatched_events": 0,
        "created_provenance": 0,
        "created_classifications": 0,
    }


def _increment(summary: dict[str, int | str], key: str) -> None:
    summary[key] = int(summary[key]) + 1


def _record_historical_candidate(
    session: Session,
    candidate: HistoricalInterviewCandidate,
    source_import: Optional[EmailImport],
    source_name: str,
) -> tuple[EmailImport | None, bool, bool, bool]:
    message = candidate.message
    imported_message = session.scalar(
        select(ImportedMessage).where(
            ImportedMessage.stable_message_identity == candidate.identity
        )
    )
    if imported_message and imported_message.provider != message.provider:
        raise ValueError("Stored message provider conflicts with provider-scoped identity")
    job = _historical_job(session, candidate, imported_message)
    source_import, provenance_created = _ensure_historical_provenance(
        session, candidate, job, source_import, source_name
    )
    classification, classification_created = _ensure_historical_classification(
        session, candidate, job
    )
    session.flush()
    observed_at = message.received_at or datetime.utcnow()
    recruiter = _historical_recruiter(session, candidate, job, observed_at)
    record_interview_evidence(
        session,
        evidence=candidate.evidence,
        identity=candidate.identity,
        classification_id=classification.id,
        provider=message.provider,
        job=job,
        recruiter=recruiter,
        observed_at=observed_at,
    )
    return source_import, bool(job), provenance_created, classification_created


def _historical_job(
    session: Session,
    candidate: HistoricalInterviewCandidate,
    imported_message: Optional[ImportedMessage],
) -> Optional[Job]:
    if imported_message and imported_message.job_id:
        return session.get(Job, imported_message.job_id)
    message = candidate.message
    return find_explicit_job(
        session, f"{message.subject} {message.body}", message.provider
    )


def _ensure_historical_provenance(
    session: Session,
    candidate: HistoricalInterviewCandidate,
    job: Optional[Job],
    source_import: Optional[EmailImport],
    source_name: str,
) -> tuple[EmailImport | None, bool]:
    if imported_message_exists(session, candidate.identity):
        return source_import, False
    message = candidate.message
    if source_import is None:
        source_import = EmailImport(
            mailbox_name=message.provider,
            source_filename=f"historical:{source_name}",
        )
        session.add(source_import)
        session.flush()
    record_imported_message(
        session,
        provider=message.provider,
        source_import_id=source_import.id,
        identity=candidate.identity,
        original_message_id=message.message_id,
        job_id=job.id if job else None,
        outcome="matched" if job else "classified",
    )
    return source_import, True


def _ensure_historical_classification(
    session: Session, candidate: HistoricalInterviewCandidate, job: Optional[Job]
) -> tuple[EmailClassification, bool]:
    classification = find_email_classification(
        session, candidate.identity, candidate.classification.classifier_version
    )
    if classification:
        if classification.classification != candidate.classification.classification.value:
            raise ValueError("Stored classification conflicts with deterministic replay")
        return classification, False
    return (
        record_email_classification(
            session,
            identity=candidate.identity,
            job_id=job.id if job else None,
            result=candidate.classification,
        ),
        True,
    )


def _historical_recruiter(
    session: Session,
    candidate: HistoricalInterviewCandidate,
    job: Optional[Job],
    observed_at: datetime,
) -> Optional[Recruiter]:
    message = candidate.message
    evidence = extract_recruiter(
        classification=candidate.classification.classification.value,
        sender=message.sender,
        subject=message.subject,
        body=message.body,
    )
    if not evidence:
        return find_message_recruiter(session, message.sender, job)
    return record_message_recruiter(
        session,
        evidence=evidence,
        identity=candidate.identity,
        job=job,
        observed_at=observed_at,
    )


def _finish_historical_import(
    source_import: Optional[EmailImport], summary: dict[str, int | str]
) -> None:
    if not source_import:
        return
    source_import.total_messages = int(summary["total_messages"])
    source_import.confirmations_found = 0
    source_import.matched_jobs = int(summary["linked_events"])
    source_import.unmatched_jobs = int(summary["unmatched_events"])


def import_yahoo_imap_messages(messages: Iterable[YahooImapMessage]) -> dict[str, object]:
    """Compatibility wrapper for the original Yahoo-specific synchronization entry point."""
    batch = list(messages)
    if any(message.provider != "yahoo" for message in batch):
        raise ValueError("Yahoo IMAP import accepts only Yahoo transport messages")
    return import_imap_messages(batch)


def import_imap_messages(messages: Iterable[YahooImapMessage]) -> dict[str, object]:
    """Feed one provider-scoped IMAP batch through the deterministic evidence pipeline."""
    batch = sorted(messages, key=lambda message: (message.uid, message.identity))
    if not batch:
        return _empty_imap_summary("")
    provider = batch[0].provider
    if provider not in ACCOUNT_MAP:
        raise ValueError("IMAP provider must be yahoo, gmail, or hotmail")
    _validate_imap_batch(batch)
    summary = _empty_imap_summary(provider)
    failures: list[dict[str, object]] = []
    with Session(engine) as session:
        cross_account_conflicts = (
            _yahoo_cross_account_conflicts(session, batch)
            if provider == "yahoo"
            else _imap_cross_account_conflicts(session, batch, provider)
        )
        batch_identities = {message.identity for message in batch}
        existing_identities = {
            identity
            for identity in session.scalars(
                select(ImportedMessage.stable_message_identity).where(
                    ImportedMessage.stable_message_identity.in_(
                        batch_identities
                    )
                )
            )
        }
        if batch_identities.issubset(existing_identities):
            summary["scanned_count"] = len(batch)
            summary["skipped_count"] = len(batch)
            return summary
        import_record = EmailImport(
            mailbox_name=provider,
            source_filename=f"imap:{provider}:{batch[0].folder}",
        )
        session.add(import_record)
        session.flush()
        for message in batch:
            if imported_message_exists(session, message.identity):
                summary["skipped_count"] = int(summary["skipped_count"]) + 1
                continue
            try:
                with session.begin_nested():
                    if provider == "yahoo":
                        application, matched = _record_yahoo_imap_message(
                            session,
                            import_record,
                            message,
                            cross_account_conflict=message.identity in cross_account_conflicts,
                        )
                    else:
                        application, matched = _record_imap_message(
                            session,
                            import_record,
                            message,
                            provider=provider,
                            cross_account_conflict=message.identity in cross_account_conflicts,
                        )
                summary["accepted_count"] = int(summary["accepted_count"]) + 1
                if application:
                    key = "matched_jobs" if matched else "unmatched_jobs"
                    summary[key] = int(summary[key]) + 1
            except Exception as exc:
                failures.append(
                    {
                        "uid": message.uid,
                        "error": f"{type(exc).__name__}: persistence rejected",
                    }
                )
        summary["scanned_count"] = len(batch)
        summary["failure_count"] = len(failures)
        summary["failures"] = failures
        summary["unresolved_messages"] = [
            {
                "uid": message.uid,
                "classification": EmailType.APPLICATION_CONFIRMATION.value,
                "reason_category": "conflicting_identity",
                "reason": "job identifier belongs to a different provider account",
            }
            for message in batch
            if message.identity in cross_account_conflicts
        ]
        import_record.total_messages = len(batch)
        import_record.confirmations_found = int(summary["accepted_count"])
        import_record.matched_jobs = int(summary["matched_jobs"])
        import_record.unmatched_jobs = int(summary["unmatched_jobs"])
        session.commit()
    return summary


def _empty_imap_summary(provider: str) -> dict[str, object]:
    return {
        "provider": provider,
        "scanned_count": 0,
        "accepted_count": 0,
        "skipped_count": 0,
        "failure_count": 0,
        "matched_jobs": 0,
        "unmatched_jobs": 0,
        "failures": [],
        "unresolved_messages": [],
    }


def _empty_yahoo_imap_summary() -> dict[str, object]:
    return _empty_imap_summary("yahoo")


def _validate_imap_batch(messages: list[YahooImapMessage]) -> None:
    first = messages[0]
    if any(
        message.provider != first.provider
        or message.account_namespace != first.account_namespace
        or message.folder != first.folder
        or message.uidvalidity != first.uidvalidity
        for message in messages
    ):
        raise ValueError("IMAP batches must use one provider, account, folder, and UIDVALIDITY")


def _validate_yahoo_imap_batch(messages: list[YahooImapMessage]) -> None:
    _validate_imap_batch(messages)
    if any(message.provider != "yahoo" for message in messages):
        raise ValueError("Yahoo IMAP batches must contain only Yahoo messages")


def _yahoo_cross_account_conflicts(
    session: Session, messages: list[YahooImapMessage]
) -> set[str]:
    """Plan cross-account identifier conflicts from the pre-import snapshot."""
    return _imap_cross_account_conflicts(session, messages, "yahoo")


def _imap_cross_account_conflicts(
    session: Session, messages: list[YahooImapMessage], provider: str
) -> set[str]:
    """Plan provider-account identifier conflicts from the pre-import snapshot."""
    conflicts: set[str] = set()
    for message in messages:
        classification = classify_email(
            subject=message.subject,
            sender=message.sender,
            body=message.text_body,
        )
        if classification.classification != EmailType.APPLICATION_CONFIRMATION:
            continue
        identifier = extract_job_id(f"{message.subject} {message.text_body}")
        if not identifier:
            continue
        existing = session.scalar(select(Job).where(Job.linkedin_job_id == identifier))
        if existing and existing.email_account and existing.email_account != provider:
            conflicts.add(message.identity)
    return conflicts


def _record_yahoo_imap_message(
    session: Session,
    import_record: EmailImport,
    message: YahooImapMessage,
    *,
    cross_account_conflict: bool = False,
) -> tuple[bool, bool]:
    return _record_imap_message(
        session,
        import_record,
        message,
        provider="yahoo",
        cross_account_conflict=cross_account_conflict,
    )


def _record_imap_message(
    session: Session,
    import_record: EmailImport,
    message: YahooImapMessage,
    *,
    provider: str,
    cross_account_conflict: bool = False,
) -> tuple[bool, bool]:
    subject = message.subject
    sender = message.sender
    body = message.text_body
    combined = f"{subject} {body}"
    classification = classify_email(subject=subject, sender=sender, body=body)
    is_application = classification.classification == EmailType.APPLICATION_CONFIRMATION
    job: Optional[Job] = None
    matched = False
    if is_application and not cross_account_conflict:
        company, title = extract_company_and_title(subject, body, sender)
        urls = extract_urls(combined)
        job, _, matched = match_or_create_application(
            session,
            mailbox_name=provider,
            company=company,
            title=title,
            applied_at=message.received_at,
            job_url=urls[0] if urls else "",
            job_id=extract_job_id(combined),
            requisition_id=extract_requisition_id(combined),
            ats_platform=infer_ats(combined),
            message_id=message.message_id,
            stable_identity=message.identity,
        )
    recruiter_evidence = extract_recruiter(
        classification=classification.classification.value,
        sender=sender,
        subject=subject,
        body=body,
    )
    interview_evidence = extract_interview(
        classification=classification.classification.value,
        subject=subject,
        body=body,
    )
    if recruiter_evidence or interview_evidence:
        job = find_explicit_job(session, combined, provider)
    session.flush()
    imported = record_imported_message(
        session,
        provider=provider,
        source_import_id=import_record.id,
        identity=message.identity,
        original_message_id=message.message_id,
        job_id=job.id if job else None,
        outcome=(
            "unmatched"
            if cross_account_conflict
            else (("matched" if matched else "unmatched") if job else "classified")
        ),
    )
    classification_record = record_email_classification(
        session,
        identity=message.identity,
        job_id=job.id if job else None,
        result=classification,
    )
    session.flush()
    recruiter = _record_imap_recruiter(
        session,
        message,
        classification.classification.value,
        job,
        recruiter_evidence,
    )
    if interview_evidence:
        record_interview_evidence(
            session,
            evidence=interview_evidence,
            identity=message.identity,
            classification_id=classification_record.id,
            provider=provider,
            job=job,
            recruiter=recruiter,
            observed_at=message.received_at or datetime.utcnow(),
        )
    _record_imap_metadata(session, message, provider=provider)
    imported.error = (
        "conflicting_identity: job identifier belongs to a different provider account"
        if cross_account_conflict
        else ""
    )
    return is_application, matched


def _record_yahoo_imap_recruiter(
    session: Session,
    message: YahooImapMessage,
    classification: str,
    job: Optional[Job],
    evidence: Optional[RecruiterEvidence],
) -> Optional[Recruiter]:
    return _record_imap_recruiter(session, message, classification, job, evidence)


def _record_imap_recruiter(
    session: Session,
    message: YahooImapMessage,
    classification: str,
    job: Optional[Job],
    evidence: Optional[RecruiterEvidence],
) -> Optional[Recruiter]:
    if evidence:
        return record_message_recruiter(
            session,
            evidence=evidence,
            identity=message.identity,
            job=job,
            observed_at=message.received_at or datetime.utcnow(),
        )
    if classification.startswith("INTERVIEW_") or classification.startswith("ASSESSMENT_"):
        return find_message_recruiter(session, message.sender, job)
    return None


def _record_imap_metadata(
    session: Session, message: YahooImapMessage, *, provider: str | None = None
) -> None:
    attachments = [
        {
            "filename": item.filename,
            "content_type": item.content_type,
            "disposition": item.disposition,
        }
        for item in message.attachments
    ]
    session.add(
        ImapMessageMetadata(
            message_identity=message.identity,
            provider=provider or message.provider,
            account_namespace=message.account_namespace,
            folder=message.folder,
            uidvalidity=message.uidvalidity,
            imap_uid=message.uid,
            subject=message.subject,
            sender=message.sender,
            received_at=message.received_at,
            imap_internal_date=message.imap_internal_date,
            requested_since_date=message.requested_since_date,
            text_body=message.text_body,
            html_fallback_used=message.html_fallback_used,
            recipients_json=json.dumps(message.recipients, separators=(",", ":")),
            attachments_json=json.dumps(attachments, separators=(",", ":")),
            created_at=datetime.utcnow(),
        )
    )

@app.get("/health")
def health():
    return {"ok": True, "version": "2.0.0"}

@app.post("/jobs/upsert")
def upsert_job(payload: JobIn):
    now = datetime.utcnow()
    with Session(engine) as session:
        existing = session.scalar(
            select(Job).where(Job.linkedin_job_id == payload.linkedin_job_id)
        )
        score = compute_score(payload)

        if existing:
            for field, value in payload.model_dump().items():
                setattr(existing, field, value)
            existing.score = score
            existing.last_seen_at = now
            job = existing
        else:
            job = Job(
                **payload.model_dump(),
                score=score,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(job)

        session.commit()
        session.refresh(job)
        return serialize(job)

@app.get("/jobs")
def list_jobs(
    max_applicants: Optional[int] = Query(default=None, ge=0),
    status: Optional[str] = None,
    search: Optional[str] = None,
    email_account: Optional[str] = None,
    role_family: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
):
    with Session(engine) as session:
        jobs = list(
            session.scalars(
                select(Job)
                .order_by(Job.score.desc(), Job.last_seen_at.desc())
                .limit(limit)
            )
        )

    result = []
    for job in jobs:
        if max_applicants is not None:
            if job.applicant_count is None:
                continue
            if job.applicant_count_is_over or job.applicant_count >= max_applicants:
                continue
        if status and job.status != status:
            continue
        if email_account and job.email_account != email_account:
            continue
        if role_family and job.role_family != role_family:
            continue
        if search:
            haystack = f"{job.title} {job.company} {job.location}".lower()
            if search.lower() not in haystack:
                continue
        result.append(serialize(job))

    return result

@app.patch("/jobs/{job_id}/status")
def update_status(job_id: int, payload: StatusUpdate):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job.status = payload.status
        if payload.notes is not None:
            job.notes = payload.notes
        session.commit()
        session.refresh(job)
        return serialize(job)

@app.delete("/jobs/{job_id}")
def delete_job(job_id: int):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        imported = bool(
            job.email_account
            or job.confirmation_message_id
            or job.application_source == "email_confirmation"
            or job.source in {"email", "yahoo_db"}
        )
        if imported:
            raise HTTPException(
                409,
                "Imported historical records cannot be hard-deleted",
            )
        session.delete(job)
        session.commit()
    return {"ok": True}

@app.post("/imports/mbox")
async def import_mbox(
    mailbox_name: str = Form(...),
    account_namespace: str = Form(""),
    file: UploadFile = File(...),
):
    mailbox_name = mailbox_name.lower().strip()
    if mailbox_name not in {"hotmail", "gmail"}:
        raise HTTPException(400, "mailbox_name must be hotmail or gmail")
    account_namespace = account_namespace.strip().casefold()
    profile = account_profile(mailbox_name, account_namespace)

    suffix = Path(file.filename or "upload.mbox").suffix or ".mbox"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        while chunk := await file.read(1024 * 1024):
            temp_file.write(chunk)
        temp_path = Path(temp_file.name)

    total_messages = 0
    confirmations_found = 0
    matched_jobs = 0
    unmatched_jobs = 0
    newly_imported = 0
    already_imported = 0
    failed = 0
    preview: list[dict] = []

    try:
        mbox = mailbox.mbox(temp_path)
        with Session(engine) as session:
            import_record = EmailImport(
                mailbox_name=mailbox_name,
                source_filename=file.filename or "",
            )
            session.add(import_record)
            session.flush()

            for message in mbox:
                total_messages += 1

                subject = decode_mime(message.get("Subject"))
                sender = decode_mime(message.get("From"))
                body = message_body(message)

                applied_at = parse_email_date(message.get("Date"))
                message_id = decode_mime(message.get("Message-ID"))
                combined = f"{subject} {body}"
                classification = classify_email(subject=subject, sender=sender, body=body)
                is_application = (
                    classification.classification == EmailType.APPLICATION_CONFIRMATION
                )
                confirmations_found += int(is_application)

                identity = stable_message_identity(
                    provider=f"{mailbox_name}:{account_namespace or mailbox_name}",
                    message_id=message_id,
                    subject=subject,
                    sender=sender,
                    received_at=applied_at,
                    body=body,
                )
                if imported_message_exists(session, identity):
                    already_imported += 1
                    continue

                try:
                    job: Optional[Job] = None
                    score = 0.0
                    matched = False
                    company = ""
                    title = ""
                    ats_platform = ""
                    requisition_id = ""
                    if is_application:
                        company, title = extract_company_and_title(subject, body, sender)
                        urls = extract_urls(combined)
                        job_url = next(
                            (
                                url
                                for url in urls
                                if any(domain in url.lower() for domain in ATS_DOMAINS)
                            ),
                            urls[0] if urls else "",
                        )
                        external_job_id = extract_job_id(combined)
                        requisition_id = extract_requisition_id(combined)
                        ats_platform = infer_ats(combined)
                        job, score, matched = match_or_create_application(
                            session,
                            mailbox_name=mailbox_name,
                            company=company,
                            title=title,
                            applied_at=applied_at,
                            job_url=job_url,
                            job_id=external_job_id,
                            requisition_id=requisition_id,
                            ats_platform=ats_platform,
                            message_id=message_id,
                            stable_identity=identity,
                            account_namespace=account_namespace,
                            inferred_role_family=(
                                infer_application_role_family(f"{subject}\n{body}") or ""
                            ),
                        )
                    recruiter_evidence = extract_recruiter(
                        classification=classification.classification.value,
                        sender=sender,
                        subject=subject,
                        body=body,
                    )
                    interview_evidence = extract_interview(
                        classification=classification.classification.value,
                        subject=subject,
                        body=body,
                    )
                    if recruiter_evidence or interview_evidence:
                        job = find_explicit_job(session, combined, mailbox_name)
                    session.flush()
                    imported_message = record_imported_message(
                        session,
                        provider=mailbox_name,
                        source_import_id=import_record.id,
                        identity=identity,
                        original_message_id=message_id,
                        job_id=job.id if job else None,
                        outcome=("matched" if matched else "unmatched") if job else "classified",
                    )
                    classification_record = record_email_classification(
                        session,
                        identity=identity,
                        job_id=job.id if job else None,
                        result=classification,
                    )
                    session.flush()
                    recruiter: Optional[Recruiter] = None
                    if recruiter_evidence:
                        recruiter = record_message_recruiter(
                            session,
                            evidence=recruiter_evidence,
                            identity=identity,
                            job=job,
                            observed_at=applied_at or datetime.utcnow(),
                        )
                    elif interview_evidence:
                        recruiter = find_message_recruiter(session, sender, job)
                    if interview_evidence:
                        try:
                            with session.begin_nested():
                                record_interview_evidence(
                                    session,
                                    evidence=interview_evidence,
                                    identity=identity,
                                    classification_id=classification_record.id,
                                    provider=mailbox_name,
                                    job=job,
                                    recruiter=recruiter,
                                    observed_at=applied_at or datetime.utcnow(),
                                )
                        except Exception as exc:
                            imported_message.error = f"interview processing failed: {exc}"
                    newly_imported += 1
                except Exception as exc:
                    session.rollback()
                    failed += 1
                    raise HTTPException(422, f"Email import failed: {exc}") from exc

                if is_application:
                    matched_jobs += int(matched)
                    unmatched_jobs += int(not matched)

                if is_application and job and len(preview) < 100:
                    preview.append(
                        {
                            "company": company,
                            "title": title,
                            "applied_at": applied_at.isoformat() if applied_at else None,
                            "mailbox": mailbox_name,
                            "role_family": profile["role_family"],
                            "matched": matched,
                            "confidence": round(score, 1),
                            "matched_job_id": job.id,
                            "ats_platform": ats_platform,
                            "requisition_id": requisition_id,
                        }
                    )

            import_record.total_messages = total_messages
            import_record.confirmations_found = confirmations_found
            import_record.matched_jobs = matched_jobs
            import_record.unmatched_jobs = unmatched_jobs
            session.commit()

        return {
            "mailbox_name": mailbox_name,
            "account_namespace": account_namespace or mailbox_name,
            "role_family": profile["role_family"],
            "resume_family": profile["resume_family"],
            "total_messages": total_messages,
            "confirmations_found": confirmations_found,
            "matched_jobs": matched_jobs,
            "unmatched_jobs": unmatched_jobs,
            "newly_imported": newly_imported,
            "already_imported": already_imported,
            "matched": matched_jobs,
            "unmatched": unmatched_jobs,
            "failed": failed,
            "preview": preview,
        }
    finally:
        temp_path.unlink(missing_ok=True)

@app.post("/imports/yahoo")
def import_yahoo(payload: YahooImportPayload):
    imported = 0
    matched = 0
    unmatched = 0
    already_imported = 0
    failed = 0

    with Session(engine) as session:
        import_record = EmailImport(
            mailbox_name="yahoo",
            source_filename="yahoo-json",
            total_messages=len(payload.records),
        )
        session.add(import_record)
        session.flush()

        for record in payload.records:
            raw_message = bool(record.subject or record.sender or record.body)
            subject = record.subject or "Application confirmation"
            sender = record.sender or record.company
            body = record.body or "Application received through Yahoo structured import"
            classification = classify_email(subject=subject, sender=sender, body=body)
            identity_body = (
                body
                if raw_message
                else "|".join(
                    [
                        record.job_url,
                        record.job_id,
                        record.requisition_id,
                        record.ats_platform,
                    ]
                )
            )
            identity = stable_message_identity(
                provider="yahoo",
                message_id=record.confirmation_message_id,
                subject=subject if raw_message else record.title,
                sender=sender if raw_message else record.company,
                received_at=record.applied_at,
                body=identity_body,
            )
            if imported_message_exists(session, identity):
                already_imported += 1
                continue

            is_application = classification.classification == EmailType.APPLICATION_CONFIRMATION
            job: Optional[Job] = None
            was_matched = False
            if is_application:
                job, _, was_matched = match_or_create_application(
                    session,
                    mailbox_name="yahoo",
                    company=record.company,
                    title=record.title,
                    applied_at=record.applied_at,
                    job_url=record.job_url,
                    job_id=record.job_id,
                    requisition_id=record.requisition_id,
                    ats_platform=record.ats_platform,
                    message_id=record.confirmation_message_id,
                    stable_identity=identity,
                )
            recruiter_evidence = extract_recruiter(
                classification=classification.classification.value,
                sender=sender,
                subject=subject,
                body=body,
            )
            interview_evidence = extract_interview(
                classification=classification.classification.value,
                subject=subject,
                body=body,
            )
            if recruiter_evidence or interview_evidence:
                job = find_explicit_job(session, f"{subject} {body}", "yahoo")
            session.flush()
            imported_message = record_imported_message(
                session,
                provider="yahoo",
                source_import_id=import_record.id,
                identity=identity,
                original_message_id=record.confirmation_message_id,
                job_id=job.id if job else None,
                outcome=("matched" if was_matched else "unmatched") if job else "classified",
            )
            classification_record = record_email_classification(
                session,
                identity=identity,
                job_id=job.id if job else None,
                result=classification,
            )
            session.flush()
            observed_at = record.applied_at or datetime.utcnow()
            recruiter: Optional[Recruiter] = None
            if recruiter_evidence:
                recruiter = record_message_recruiter(
                    session,
                    evidence=recruiter_evidence,
                    identity=identity,
                    job=job,
                    observed_at=observed_at,
                )
            elif interview_evidence:
                recruiter = find_message_recruiter(session, sender, job)
            if interview_evidence:
                try:
                    with session.begin_nested():
                        record_interview_evidence(
                            session,
                            evidence=interview_evidence,
                            identity=identity,
                            classification_id=classification_record.id,
                            provider="yahoo",
                            job=job,
                            recruiter=recruiter,
                            observed_at=observed_at,
                        )
                except Exception as exc:
                    imported_message.error = f"interview processing failed: {exc}"
            imported += 1
            matched += int(is_application and was_matched)
            unmatched += int(is_application and not was_matched)

        import_record.confirmations_found = imported + already_imported
        import_record.matched_jobs = matched
        import_record.unmatched_jobs = unmatched
        session.commit()

    return {
        "imported": imported,
        "matched_jobs": matched,
        "unmatched_jobs": unmatched,
        "newly_imported": imported,
        "already_imported": already_imported,
        "matched": matched,
        "unmatched": unmatched,
        "failed": failed,
        "email_account": "yahoo",
        "role_family": ACCOUNT_MAP["yahoo"]["role_family"],
        "resume_family": ACCOUNT_MAP["yahoo"]["resume_family"],
    }

@app.get("/imports")
def list_imports():
    with Session(engine) as session:
        records = list(
            session.scalars(
                select(EmailImport).order_by(EmailImport.imported_at.desc())
            )
        )

    return [
        {
            "id": record.id,
            "mailbox_name": record.mailbox_name,
            "source_filename": record.source_filename,
            "imported_at": record.imported_at.isoformat(),
            "total_messages": record.total_messages,
            "confirmations_found": record.confirmations_found,
            "matched_jobs": record.matched_jobs,
            "unmatched_jobs": record.unmatched_jobs,
        }
        for record in records
    ]


@app.get("/sync/status")
def synchronization_status():
    """Return non-secret, read-only provider checkpoint and evidence status."""
    return provider_sync_status(DB_PATH)


def _domain_write(operation, *args):
    try:
        return operation(DB_PATH, *args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (RuntimeError, sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/applications")
def applications_index():
    return list_applications(DB_PATH)


@app.post("/applications", status_code=201)
def applications_create(payload: ApplicationInput):
    return _domain_write(create_application, payload.model_dump(mode="json"))


@app.get("/applications/{application_id}")
def applications_show(application_id: int):
    return _domain_write(get_application, application_id)


@app.patch("/applications/{application_id}")
def applications_update(application_id: int, payload: ApplicationUpdate):
    return _domain_write(
        update_application,
        application_id,
        payload.model_dump(mode="json", exclude_unset=True),
    )


@app.get("/companies")
def companies_index():
    return list_companies(DB_PATH)


@app.post("/companies", status_code=201)
def companies_create(payload: CompanyInput):
    return _domain_write(create_company, payload.model_dump(mode="json"))


@app.get("/companies/{company_id}/timeline")
def companies_timeline(company_id: int):
    return _domain_write(company_timeline, company_id)


@app.get("/resumes")
def resumes_index():
    return list_resumes(DB_PATH)


@app.post("/resumes", status_code=201)
def resumes_create(payload: ResumeInput):
    return _domain_write(create_resume, payload.model_dump(mode="json"))


@app.get("/job-descriptions")
def job_descriptions_index(job_id: Optional[int] = None):
    return list_job_descriptions(DB_PATH, job_id)


@app.post("/job-descriptions", status_code=201)
def job_descriptions_create(payload: JobDescriptionInput):
    return _domain_write(create_job_description, payload.model_dump(mode="json"))


@app.get("/offers")
def offers_index():
    return list_offers(DB_PATH)


@app.post("/offers", status_code=201)
def offers_create(payload: OfferInput):
    return _domain_write(create_offer, payload.model_dump(mode="json"))


@app.patch("/offers/{offer_id}")
def offers_update(offer_id: int, payload: OfferUpdate):
    return _domain_write(
        update_offer,
        offer_id,
        payload.model_dump(mode="json", exclude_unset=True),
    )


@app.get("/notes")
def notes_index(entity_type: str, entity_id: int):
    return list_notes(DB_PATH, entity_type, entity_id)


@app.post("/notes", status_code=201)
def notes_create(payload: NoteInput):
    return _domain_write(create_note, payload.model_dump(mode="json"))


@app.post("/interactions", status_code=201)
def interactions_create(payload: InteractionInput):
    return _domain_write(create_interaction, payload.model_dump(mode="json"))


@app.put("/recruiters/{recruiter_id}/relationship")
def recruiter_relationship_update(
    recruiter_id: int, payload: RecruiterRelationshipInput
):
    return _domain_write(
        upsert_recruiter_relationship,
        recruiter_id,
        payload.model_dump(mode="json"),
    )


@app.get("/analytics/version1")
def analytics_version1():
    return version1_analytics(DB_PATH)


@app.get("/settings/status")
def settings_status():
    return {
        "database_path": str(DB_PATH),
        "schema_ready": schema_ready(DB_PATH),
        "providers": provider_sync_status(DB_PATH),
    }


@app.get("/email-classifications")
def list_email_classifications(
    classification: Optional[str] = None,
    provider: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
):
    query = select(EmailClassification).order_by(EmailClassification.id.desc()).limit(limit)
    if classification:
        query = query.where(EmailClassification.classification == classification.upper())
    if provider:
        query = query.join(
            ImportedMessage,
            ImportedMessage.stable_message_identity == EmailClassification.message_identity,
        ).where(ImportedMessage.provider == provider.lower())
    with Session(engine) as session:
        records = list(session.scalars(query))
    return [
        {
            "id": record.id,
            "message_identity": record.message_identity,
            "job_id": record.job_id,
            "classification": record.classification,
            "confidence": record.confidence,
            "classifier_version": record.classifier_version,
            "reasons": json.loads(record.reason_json),
            "created_at": record.created_at.isoformat(),
        }
        for record in records
    ]


def serialize_recruiter(session: Session, recruiter: Recruiter) -> dict:
    companies = list(
        session.scalars(
            select(RecruiterCompanyLink).where(
                RecruiterCompanyLink.recruiter_id == recruiter.id
            )
        )
    )
    emails = list(
        session.scalars(
            select(RecruiterEmailAddress).where(
                RecruiterEmailAddress.recruiter_id == recruiter.id
            )
        )
    )
    job_links = list(
        session.scalars(
            select(RecruiterJobLink).where(RecruiterJobLink.recruiter_id == recruiter.id)
        )
    )
    return {
        "id": recruiter.id,
        "name": recruiter.name,
        "title": recruiter.title,
        "signature": recruiter.signature,
        "linkedin_url": recruiter.linkedin_url,
        "phone": recruiter.phone,
        "companies": [item.company_name for item in companies],
        "emails": [item.email for item in emails],
        "contact_count": len(emails),
        "job_links": [
            {
                "job_id": item.job_id,
                "relationship_type": item.relationship_type,
                "source_message_identity": item.source_message_identity,
                "first_seen_at": item.first_seen_at.isoformat(),
                "last_seen_at": item.last_seen_at.isoformat(),
            }
            for item in job_links
        ],
        "relationship": get_recruiter_relationship(DB_PATH, recruiter.id),
        "first_seen_at": recruiter.first_seen_at.isoformat(),
        "last_seen_at": recruiter.last_seen_at.isoformat(),
    }


@app.get("/recruiters")
def list_recruiters(
    company: Optional[str] = None,
    email: Optional[str] = None,
    limit: int = Query(default=500, ge=1, le=5000),
):
    query = select(Recruiter).order_by(Recruiter.last_seen_at.desc()).limit(limit)
    if company:
        query = query.join(
            RecruiterCompanyLink,
            RecruiterCompanyLink.recruiter_id == Recruiter.id,
        ).where(
            RecruiterCompanyLink.normalized_company_name
            == normalize_recruiter_company(company)
        )
    if email:
        query = query.join(
            RecruiterEmailAddress,
            RecruiterEmailAddress.recruiter_id == Recruiter.id,
        ).where(RecruiterEmailAddress.normalized_email == email.strip().casefold())
    with Session(engine) as session:
        recruiters = list(session.scalars(query).unique())
        return [serialize_recruiter(session, recruiter) for recruiter in recruiters]


@app.get("/recruiters/{recruiter_id}")
def get_recruiter(recruiter_id: int):
    with Session(engine) as session:
        recruiter = session.get(Recruiter, recruiter_id)
        if recruiter is None:
            raise HTTPException(404, "Recruiter not found")
        return serialize_recruiter(session, recruiter)


def serialize_interview(session: Session, interview: Interview, *, detail: bool = False) -> dict:
    job = session.get(Job, interview.job_id)
    recruiter = session.get(Recruiter, interview.recruiter_id) if interview.recruiter_id else None
    events = list(
        session.scalars(
            select(InterviewEvent)
            .where(InterviewEvent.interview_id == interview.id)
            .order_by(InterviewEvent.occurred_at, InterviewEvent.id)
        )
    )
    result = {
        "id": interview.id,
        "job_id": interview.job_id,
        "recruiter_id": interview.recruiter_id,
        "interview_type": interview.interview_type,
        "status": interview.status,
        "scheduled_start": _iso(interview.scheduled_start),
        "scheduled_end": _iso(interview.scheduled_end),
        "timezone": interview.timezone,
        "location_type": interview.location_type,
        "location_text": interview.location_text,
        "meeting_url": interview.meeting_url,
        "title": interview.title,
        "first_source_message_identity": interview.first_source_message_identity,
        "first_seen_at": _iso(interview.first_seen_at),
        "last_seen_at": _iso(interview.last_seen_at),
        "job": {"id": job.id, "title": job.title, "company": job.company} if job else None,
        "recruiter": (
            {"id": recruiter.id, "name": recruiter.name, "title": recruiter.title}
            if recruiter
            else None
        ),
        "event_count": len(events),
    }
    if detail:
        result["events"] = [serialize_interview_event(event) for event in events]
    return result


def serialize_interview_event(event: InterviewEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "occurred_at": _iso(event.occurred_at),
        "scheduled_start": _iso(event.extracted_start),
        "scheduled_end": _iso(event.extracted_end),
        "timezone": event.timezone,
        "location_type": event.location_type,
        "location_text": event.location_text,
        "meeting_url": event.meeting_url,
        "provider": event.provider,
        "source_message_identity": event.source_message_identity,
        "classification_id": event.classification_id,
        "extractor_version": event.extractor_version,
        "evidence": json.loads(event.evidence_json),
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _interview_query(
    *,
    status: Optional[str],
    interview_type: Optional[str],
    job_id: Optional[int],
    recruiter_id: Optional[int],
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    provider: Optional[str],
    company: Optional[str],
    upcoming: bool,
):
    query = select(Interview)
    for column, value in (
        (Interview.status, status),
        (Interview.interview_type, interview_type),
        (Interview.job_id, job_id),
        (Interview.recruiter_id, recruiter_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if from_date:
        query = query.where(Interview.scheduled_start >= from_date)
    if to_date:
        query = query.where(Interview.scheduled_start <= to_date)
    if upcoming:
        query = query.where(
            Interview.scheduled_start >= datetime.utcnow(), Interview.status != "cancelled"
        )
    if provider:
        query = query.join(
            InterviewEvent, InterviewEvent.interview_id == Interview.id
        ).where(InterviewEvent.provider == provider.lower())
    if company:
        query = query.join(Job, Job.id == Interview.job_id).where(Job.company == company)
    return query.distinct().order_by(Interview.scheduled_start, Interview.id)


@app.get("/interviews")
def list_interviews(
    status: Optional[str] = None,
    interview_type: Optional[str] = None,
    job_id: Optional[int] = None,
    recruiter_id: Optional[int] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    provider: Optional[str] = None,
    company: Optional[str] = None,
    upcoming: bool = False,
    limit: int = Query(default=500, ge=1, le=5000),
):
    query = _interview_query(
        status=status,
        interview_type=interview_type,
        job_id=job_id,
        recruiter_id=recruiter_id,
        from_date=from_date,
        to_date=to_date,
        provider=provider,
        company=company,
        upcoming=upcoming,
    ).limit(limit)
    with Session(engine) as session:
        return [serialize_interview(session, item) for item in session.scalars(query).unique()]


@app.get("/interviews/upcoming")
def upcoming_interviews(limit: int = Query(default=100, ge=1, le=500)):
    return list_interviews(upcoming=True, limit=limit)


@app.get("/interviews/{interview_id}")
def get_interview(interview_id: int):
    with Session(engine) as session:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(404, "Interview not found")
        return serialize_interview(session, interview, detail=True)

@app.get("/jobs/export.csv")
def export_csv(
    max_applicants: Optional[int] = Query(default=None, ge=0),
    email_account: Optional[str] = None,
):
    jobs = list_jobs(
        max_applicants=max_applicants,
        email_account=email_account,
        limit=5000,
    )
    output = io.StringIO()
    fields = [
        "score",
        "applicant_count",
        "applicant_count_is_over",
        "title",
        "company",
        "location",
        "salary_text",
        "email_account",
        "role_family",
        "resume_family",
        "applied_at",
        "ats_platform",
        "requisition_id",
        "import_confidence",
        "status",
        "url",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for job in jobs:
        writer.writerow({field: job.get(field, "") for field in fields})

    output.seek(0)
    filename = f"job-intelligence-{datetime.utcnow().date().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "static" / "index.html")

@app.get('/analytics/overview')
def analytics_overview():
    return corrected_analytics_overview(DB_PATH)

@app.get('/analytics/timeline')
def analytics_timeline():
    return corrected_analytics_timeline(DB_PATH)

@app.get('/analytics/roles')
def analytics_roles():
    return corrected_analytics_roles(DB_PATH)

@app.get('/analytics/companies')
def analytics_companies(limit:int=50):
    return corrected_analytics_companies(DB_PATH, limit)

@app.get('/analytics/attributed')
def analytics_attributed():
    snapshot = load_attributed_snapshot(ATTRIBUTED_ANALYTICS_PATH)
    return {"available": snapshot is not None, "snapshot": snapshot}


@app.get('/analytics/unlinked-evidence')
def analytics_unlinked_evidence(limit: int = Query(default=100, ge=1, le=500)):
    """Provide a safe, read-only worklist for deterministic linkage review."""
    return list_unlinked_evidence(DB_PATH, limit=limit)
