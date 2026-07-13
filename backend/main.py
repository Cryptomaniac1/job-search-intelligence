
from __future__ import annotations

import csv
import io
import json
import mailbox
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, select
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
    from backend.app.services.import_identity import stable_message_identity
    from backend.app.services.recruiter_crm import (
        RecruiterEvidence,
        extract_recruiter,
        normalize_company as normalize_recruiter_company,
    )
except ModuleNotFoundError:  # Supports the existing `cd backend && uvicorn main:app` command.
    from app.database.paths import initialize_database_if_missing, resolve_database_path
    from app.services.email_classification import ClassificationResult, EmailType, classify_email
    from app.services.import_identity import stable_message_identity
    from app.services.recruiter_crm import (
        RecruiterEvidence,
        extract_recruiter,
        normalize_company as normalize_recruiter_company,
    )

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = resolve_database_path()
initialize_database_if_missing(DB_PATH)

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
    re.compile(r"(?:position|role|job)\s*[:\-]\s*(.{3,180})", re.I),
    re.compile(r"application (?:for|to)\s+(.{3,180})", re.I),
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

    text = "\n".join(parts)
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

def extract_company_and_title(subject: str, body: str, sender: str) -> tuple[str, str]:
    combined = f"{subject} {body}"

    title = ""
    for pattern in TITLE_PATTERNS:
        match = pattern.search(combined)
        if match:
            title = match.group(1)
            title = re.split(r"[|•\n\r]", title)[0]
            title = re.sub(r"\s{2,}", " ", title).strip(" .:-")
            if len(title) > 180:
                title = ""
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
) -> tuple[Job, float, bool]:
    account = ACCOUNT_MAP[mailbox_name]
    jobs = list(session.scalars(select(Job)))

    best_job: Optional[Job] = None
    best_score = 0.0

    for job in jobs:
        if job.email_account and job.email_account != mailbox_name:
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

    matched = bool(best_job and best_score >= 45)

    created = not matched
    if created:
        synthetic_id = job_id or f"email-{mailbox_name}-{stable_identity.removeprefix('v1:')[:32]}"
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
        best_job.email_account = mailbox_name
    if not best_job.role_family:
        best_job.role_family = account["role_family"]
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
) -> None:
    session.add(
        ImportedMessage(
            provider=provider,
            source_import_id=source_import_id,
            stable_message_identity=identity,
            original_message_id=original_message_id,
            job_id=job_id,
            outcome=outcome,
            error=error,
        )
    )


def record_email_classification(
    session: Session,
    *,
    identity: str,
    job_id: Optional[int],
    result: ClassificationResult,
) -> None:
    session.add(
        EmailClassification(
            message_identity=identity,
            job_id=job_id,
            classification=result.classification.value,
            confidence=result.confidence,
            classifier_version=result.classifier_version,
            reason_json=json.dumps(list(result.reasons), separators=(",", ":")),
        )
    )


def find_explicit_job(session: Session, content: str) -> Optional[Job]:
    """Resolve a job only from an explicit job or requisition identifier."""
    identifiers = {extract_job_id(content), extract_requisition_id(content)} - {""}
    for identifier in identifiers:
        job = session.scalar(
            select(Job).where(
                (Job.linkedin_job_id == identifier) | (Job.requisition_id == identifier)
            )
        )
        if job:
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
    file: UploadFile = File(...),
):
    mailbox_name = mailbox_name.lower().strip()
    if mailbox_name not in {"hotmail", "gmail"}:
        raise HTTPException(400, "mailbox_name must be hotmail or gmail")

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
                    provider=mailbox_name,
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
                        )
                    recruiter_evidence = extract_recruiter(
                        classification=classification.classification.value,
                        sender=sender,
                        subject=subject,
                        body=body,
                    )
                    if recruiter_evidence:
                        job = find_explicit_job(session, combined)
                    session.flush()
                    record_imported_message(
                        session,
                        provider=mailbox_name,
                        source_import_id=import_record.id,
                        identity=identity,
                        original_message_id=message_id,
                        job_id=job.id if job else None,
                        outcome=("matched" if matched else "unmatched") if job else "classified",
                    )
                    record_email_classification(
                        session,
                        identity=identity,
                        job_id=job.id if job else None,
                        result=classification,
                    )
                    session.flush()
                    if recruiter_evidence:
                        record_recruiter(
                            session,
                            evidence=recruiter_evidence,
                            identity=identity,
                            job=job,
                            observed_at=applied_at or datetime.utcnow(),
                        )
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
                            "role_family": ACCOUNT_MAP[mailbox_name]["role_family"],
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
            "role_family": ACCOUNT_MAP[mailbox_name]["role_family"],
            "resume_family": ACCOUNT_MAP[mailbox_name]["resume_family"],
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
            classification = classify_email(
                subject="Application confirmation",
                sender=record.company,
                body="Application received through Yahoo structured import",
            )
            identity = stable_message_identity(
                provider="yahoo",
                message_id=record.confirmation_message_id,
                subject=record.title,
                sender=record.company,
                received_at=record.applied_at,
                body="|".join(
                    [
                        record.job_url,
                        record.job_id,
                        record.requisition_id,
                        record.ats_platform,
                    ]
                ),
            )
            if imported_message_exists(session, identity):
                already_imported += 1
                continue

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
            session.flush()
            record_imported_message(
                session,
                provider="yahoo",
                source_import_id=import_record.id,
                identity=identity,
                original_message_id=record.confirmation_message_id,
                job_id=job.id,
                outcome="matched" if was_matched else "unmatched",
            )
            record_email_classification(
                session,
                identity=identity,
                job_id=job.id,
                result=classification,
            )
            imported += 1
            matched += int(was_matched)
            unmatched += int(not was_matched)

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
    with Session(engine) as session:
        jobs = list(session.scalars(select(Job)))
    now = datetime.utcnow()
    last30 = now.timestamp() - 30*86400
    def is_recent(j):
        dt = j.applied_at or j.first_seen_at
        return bool(dt and dt.timestamp() >= last30)
    def stage_count(items, names):
        return sum(1 for j in items if (j.status or '').lower() in names)
    recent = [j for j in jobs if is_recent(j)]
    total = len(jobs)
    return {
        'all_time': {
            'applications': total,
            'recruiter_replies': stage_count(jobs, {'recruiter','interview','offer'}),
            'interviews': stage_count(jobs, {'interview','offer'}),
            'offers': stage_count(jobs, {'offer'}),
            'rejections': stage_count(jobs, {'rejected'}),
        },
        'last_30_days': {
            'applications': len(recent),
            'recruiter_replies': stage_count(recent, {'recruiter','interview','offer'}),
            'interviews': stage_count(recent, {'interview','offer'}),
            'offers': stage_count(recent, {'offer'}),
            'rejections': stage_count(recent, {'rejected'}),
        }
    }

@app.get('/analytics/timeline')
def analytics_timeline():
    with Session(engine) as session:
        jobs = list(session.scalars(select(Job)))
    buckets = {}
    for j in jobs:
        dt = j.applied_at or j.first_seen_at
        if not dt: continue
        key = dt.strftime('%Y-%m')
        b = buckets.setdefault(key, {'period': key, 'applications':0, 'recruiter_replies':0, 'interviews':0, 'offers':0})
        b['applications'] += 1
        s = (j.status or '').lower()
        if s in {'recruiter','interview','offer'}: b['recruiter_replies'] += 1
        if s in {'interview','offer'}: b['interviews'] += 1
        if s == 'offer': b['offers'] += 1
    return [buckets[k] for k in sorted(buckets)]

@app.get('/analytics/roles')
def analytics_roles():
    with Session(engine) as session:
        jobs = list(session.scalars(select(Job)))
    groups = {}
    for j in jobs:
        key = j.role_family or 'Unclassified'
        g = groups.setdefault(key, {'role_family':key,'applications':0,'recruiter_replies':0,'interviews':0,'offers':0})
        g['applications'] += 1
        s=(j.status or '').lower()
        if s in {'recruiter','interview','offer'}: g['recruiter_replies'] += 1
        if s in {'interview','offer'}: g['interviews'] += 1
        if s=='offer': g['offers'] += 1
    out=[]
    for g in groups.values():
        a=max(g['applications'],1)
        g['reply_rate']=round(g['recruiter_replies']/a*100,1)
        g['interview_rate']=round(g['interviews']/a*100,1)
        g['offer_rate']=round(g['offers']/a*100,2)
        out.append(g)
    return sorted(out,key=lambda x:x['applications'],reverse=True)

@app.get('/analytics/companies')
def analytics_companies(limit:int=50):
    with Session(engine) as session:
        jobs = list(session.scalars(select(Job)))
    groups={}
    for j in jobs:
        key=(j.company or 'Unknown').strip()
        g=groups.setdefault(key,{'company':key,'applications':0,'recruiter_replies':0,'interviews':0,'offers':0,'last_activity':None})
        g['applications']+=1
        s=(j.status or '').lower()
        if s in {'recruiter','interview','offer'}: g['recruiter_replies']+=1
        if s in {'interview','offer'}: g['interviews']+=1
        if s=='offer': g['offers']+=1
        dt=j.last_seen_at or j.applied_at or j.first_seen_at
        if dt and (not g['last_activity'] or dt.isoformat()>g['last_activity']): g['last_activity']=dt.isoformat()
    return sorted(groups.values(),key=lambda x:(x['interviews'],x['applications']),reverse=True)[:limit]
