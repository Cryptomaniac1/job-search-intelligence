"""Version 1 daily-use domain storage built additively beside legacy jobs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WORD = re.compile(r"[a-z0-9+#.]{3,}")
DOMAIN_TABLES = {
    "applications",
    "companies",
    "interactions",
    "job_descriptions",
    "notes",
    "offers",
    "recruiter_relationships",
    "resumes",
}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def normalize_company(value: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", value.casefold())
    suffixes = {"corp", "corporation", "inc", "incorporated", "llc", "ltd", "limited"}
    return " ".join(part for part in text.split() if part not in suffixes)


@contextmanager
def _connection(path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if not write:
        connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
        if write:
            connection.commit()
    finally:
        connection.close()


def schema_ready(path: Path) -> bool:
    with _connection(path) as connection:
        existing = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    return DOMAIN_TABLES <= existing


def _require_schema(path: Path) -> None:
    if not schema_ready(path):
        raise RuntimeError("Version 1 schema requires the current Alembic revision")


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    value = cursor.fetchone()
    return dict(value) if value else None


def _lastrowid(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a new row identifier")
    return cursor.lastrowid


def _company_id(connection: sqlite3.Connection, name: str) -> int | None:
    normalized = normalize_company(name)
    if not normalized:
        return None
    existing = connection.execute(
        "SELECT id FROM companies WHERE normalized_name = ?", (normalized,)
    ).fetchone()
    if existing:
        return int(existing[0])
    now = _now()
    cursor = connection.execute(
        "INSERT INTO companies (name, normalized_name, created_at, updated_at) VALUES (?,?,?,?)",
        (name.strip(), normalized, now, now),
    )
    return _lastrowid(cursor)


def create_company(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    now = _now()
    normalized = normalize_company(str(payload["name"]))
    with _connection(path, write=True) as connection:
        connection.execute(
            """INSERT INTO companies
               (name, normalized_name, website, industry, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(normalized_name) DO UPDATE SET
                 website=excluded.website, industry=excluded.industry,
                 notes=excluded.notes, updated_at=excluded.updated_at""",
            (
                payload["name"],
                normalized,
                payload.get("website", ""),
                payload.get("industry", ""),
                payload.get("notes", ""),
                now,
                now,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM companies WHERE normalized_name = ?", (normalized,)
            ).fetchone()
        )


def list_companies(path: Path) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        return _rows(
            connection.execute(
                """SELECT c.*,
                    (SELECT COUNT(*) FROM applications a WHERE a.company_id=c.id) application_count,
                    (SELECT COUNT(*) FROM interactions i WHERE i.company_id=c.id) interaction_count
                   FROM companies c ORDER BY c.name COLLATE NOCASE"""
            )
        )


def _resume_score(connection: sqlite3.Connection, resume_id: int | None, job_id: int) -> float:
    if resume_id is None:
        return 0.0
    resume = connection.execute(
        "SELECT content_text, tags_json FROM resumes WHERE id=?", (resume_id,)
    ).fetchone()
    job = connection.execute("SELECT title, description FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not resume or not job:
        return 0.0
    resume_words = set(WORD.findall(f"{resume[0]} {resume[1]}".casefold()))
    job_words = set(WORD.findall(f"{job[0]} {job[1]}".casefold()))
    return round(100 * len(resume_words & job_words) / max(1, len(job_words)), 2)


def create_application(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    with _connection(path, write=True) as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id=?", (payload["job_id"],)).fetchone()
        if not job:
            raise LookupError("Job not found")
        company_id = _company_id(connection, str(job["company"]))
        score = _resume_score(connection, payload.get("resume_id"), int(job["id"]))
        now = _now()
        connection.execute(
            """INSERT INTO applications
               (job_id, company_id, resume_id, status, applied_at, source, match_score,
                notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                job["id"],
                company_id,
                payload.get("resume_id"),
                payload.get("status", "applied"),
                payload.get("applied_at") or job["applied_at"],
                payload.get("source") or job["source"],
                score,
                payload.get("notes", ""),
                now,
                now,
            ),
        )
        application_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        _add_interaction(
            connection,
            {
                "company_id": company_id,
                "application_id": application_id,
                "job_id": job["id"],
                "interaction_type": "application",
                "occurred_at": payload.get("applied_at") or now,
                "summary": f"Application created: {job['title']}",
            },
        )
    return get_application(path, application_id)


def list_applications(path: Path) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        return _rows(
            connection.execute(
                """SELECT a.*, j.title job_title, j.company company, r.name resume_name
                   FROM applications a JOIN jobs j ON j.id=a.job_id
                   LEFT JOIN resumes r ON r.id=a.resume_id
                   ORDER BY COALESCE(a.applied_at,a.created_at) DESC"""
            )
        )


def get_application(path: Path, application_id: int) -> dict[str, Any]:
    with _connection(path) as connection:
        result = _row(
            connection.execute(
                """SELECT a.*, j.title job_title, j.company company, r.name resume_name
                   FROM applications a JOIN jobs j ON j.id=a.job_id
                   LEFT JOIN resumes r ON r.id=a.resume_id WHERE a.id=?""",
                (application_id,),
            )
        )
    if not result:
        raise LookupError("Application not found")
    return result


def update_application(path: Path, application_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"resume_id", "status", "applied_at", "source", "notes"}
    values = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if not values:
        return get_application(path, application_id)
    with _connection(path, write=True) as connection:
        current = connection.execute(
            "SELECT * FROM applications WHERE id=?", (application_id,)
        ).fetchone()
        if not current:
            raise LookupError("Application not found")
        values["updated_at"] = _now()
        if "resume_id" in values:
            values["match_score"] = _resume_score(
                connection, values["resume_id"], current["job_id"]
            )
        assignments = ", ".join(f"{key}=?" for key in values)
        connection.execute(
            f"UPDATE applications SET {assignments} WHERE id=?",
            (*values.values(), application_id),
        )
    return get_application(path, application_id)


def create_resume(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    now = _now()
    with _connection(path, write=True) as connection:
        cursor = connection.execute(
            """INSERT INTO resumes
               (name, version, family, tags_json, industries_json, content_text, active,
                created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                payload["name"],
                payload["version"],
                payload.get("family", ""),
                json.dumps(payload.get("tags", [])),
                json.dumps(payload.get("industries", [])),
                payload.get("content_text", ""),
                bool(payload.get("active", True)),
                now,
                now,
            ),
        )
        resume_id = _lastrowid(cursor)
    return next(item for item in list_resumes(path) if item["id"] == resume_id)


def list_resumes(path: Path) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        rows = _rows(
            connection.execute(
                """SELECT r.*,
                    (SELECT COUNT(*) FROM applications a WHERE a.resume_id=r.id) application_count
                   FROM resumes r ORDER BY r.active DESC, r.updated_at DESC"""
            )
        )
    for row in rows:
        row["tags"] = json.loads(row.pop("tags_json"))
        row["industries"] = json.loads(row.pop("industries_json"))
    return rows


def _keywords(text: str, limit: int = 30) -> list[str]:
    frequencies: dict[str, int] = {}
    for word in WORD.findall(text.casefold()):
        frequencies[word] = frequencies.get(word, 0) + 1
    return [
        word
        for word, _ in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def create_job_description(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    raw_text = str(payload["raw_text"])
    source_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    skills = _keywords(raw_text)
    requirements = [line.strip() for line in raw_text.splitlines() if "require" in line.casefold()][
        :20
    ]
    now = _now()
    with _connection(path, write=True) as connection:
        cursor = connection.execute(
            """INSERT INTO job_descriptions
               (job_id, source_type, source_url, source_hash, raw_text, source_metadata_json,
                requirements_json, skills_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                payload["job_id"],
                payload.get("source_type", "text"),
                payload.get("source_url", ""),
                source_hash,
                raw_text,
                json.dumps(payload.get("source_metadata", {})),
                json.dumps(requirements),
                json.dumps(skills),
                now,
                now,
            ),
        )
        description_id = _lastrowid(cursor)
    return get_job_description(path, description_id)


def get_job_description(path: Path, description_id: int) -> dict[str, Any]:
    with _connection(path) as connection:
        result = _row(
            connection.execute("SELECT * FROM job_descriptions WHERE id=?", (description_id,))
        )
    if not result:
        raise LookupError("Job description not found")
    for key in ("source_metadata_json", "requirements_json", "skills_json"):
        result[key.removesuffix("_json")] = json.loads(result.pop(key))
    return result


def list_job_descriptions(path: Path, job_id: int | None = None) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    query = "SELECT id FROM job_descriptions"
    params: tuple[object, ...] = ()
    if job_id is not None:
        query += " WHERE job_id=?"
        params = (job_id,)
    query += " ORDER BY created_at DESC"
    with _connection(path) as connection:
        ids = [int(row[0]) for row in connection.execute(query, params)]
    return [get_job_description(path, item) for item in ids]


def create_offer(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    now = _now()
    with _connection(path, write=True) as connection:
        application = connection.execute(
            "SELECT * FROM applications WHERE id=?", (payload["application_id"],)
        ).fetchone()
        if not application:
            raise LookupError("Application not found")
        cursor = connection.execute(
            """INSERT INTO offers
               (application_id, job_id, status, base_salary, bonus, equity, currency,
                offered_at, expires_at, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                application["id"],
                application["job_id"],
                payload.get("status", "received"),
                payload.get("base_salary"),
                payload.get("bonus"),
                payload.get("equity", ""),
                payload.get("currency", "USD"),
                payload.get("offered_at"),
                payload.get("expires_at"),
                payload.get("notes", ""),
                now,
                now,
            ),
        )
        offer_id = _lastrowid(cursor)
        _add_interaction(
            connection,
            {
                "company_id": application["company_id"],
                "application_id": application["id"],
                "job_id": application["job_id"],
                "offer_id": offer_id,
                "interaction_type": "offer",
                "occurred_at": payload.get("offered_at") or now,
                "summary": "Offer received",
            },
        )
    return get_offer(path, offer_id)


def list_offers(path: Path) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        return _rows(
            connection.execute(
                """SELECT o.*, j.title job_title, j.company company
                   FROM offers o JOIN jobs j ON j.id=o.job_id ORDER BY o.created_at DESC"""
            )
        )


def get_offer(path: Path, offer_id: int) -> dict[str, Any]:
    result = next((item for item in list_offers(path) if item["id"] == offer_id), None)
    if not result:
        raise LookupError("Offer not found")
    return result


def update_offer(path: Path, offer_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "base_salary", "bonus", "equity", "currency", "expires_at", "notes"}
    values = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if values:
        values["updated_at"] = _now()
        with _connection(path, write=True) as connection:
            assignments = ", ".join(f"{key}=?" for key in values)
            cursor = connection.execute(
                f"UPDATE offers SET {assignments} WHERE id=?", (*values.values(), offer_id)
            )
            if not cursor.rowcount:
                raise LookupError("Offer not found")
    return get_offer(path, offer_id)


def create_note(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    now = _now()
    with _connection(path, write=True) as connection:
        cursor = connection.execute(
            """INSERT INTO notes
               (entity_type,entity_id,body,created_at,updated_at) VALUES (?,?,?,?,?)""",
            (payload["entity_type"], payload["entity_id"], payload["body"], now, now),
        )
        note_id = _lastrowid(cursor)
        return dict(connection.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())


def list_notes(path: Path, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        return _rows(
            connection.execute(
                "SELECT * FROM notes WHERE entity_type=? AND entity_id=? ORDER BY created_at DESC",
                (entity_type, entity_id),
            )
        )


def _add_interaction(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    columns = (
        "company_id",
        "application_id",
        "recruiter_id",
        "job_id",
        "interview_id",
        "offer_id",
        "source_message_identity",
        "interaction_type",
        "occurred_at",
        "summary",
        "immutable_evidence",
        "created_at",
    )
    values = [payload.get(column) for column in columns]
    values[-2] = bool(payload.get("immutable_evidence", False))
    values[-1] = _now()
    cursor = connection.execute(
        f"INSERT INTO interactions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        values,
    )
    return _lastrowid(cursor)


def create_interaction(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _require_schema(path)
    with _connection(path, write=True) as connection:
        interaction_id = _add_interaction(connection, payload)
        return dict(
            connection.execute(
                "SELECT * FROM interactions WHERE id=?", (interaction_id,)
            ).fetchone()
        )


def company_timeline(path: Path, company_id: int) -> list[dict[str, Any]]:
    if not schema_ready(path):
        return []
    with _connection(path) as connection:
        company_name, normalized_name = _company_context(connection, company_id)
        rows = _rows(
            connection.execute(
                """SELECT occurred_at, interaction_type type, summary, immutable_evidence
                   FROM interactions WHERE company_id=?
                   UNION ALL
                   SELECT COALESCE(a.applied_at,a.created_at), 'application', j.title, 0
                   FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.company_id=?
                   UNION ALL
                   SELECT i.scheduled_start, 'interview', COALESCE(i.title,i.interview_type), 1
                   FROM interviews i JOIN jobs j ON j.id=i.job_id WHERE lower(j.company)=lower(?)
                   UNION ALL
                   SELECT im.imported_at, 'email', COALESCE(ec.classification,im.outcome), 1
                   FROM imported_messages im JOIN jobs j ON j.id=im.job_id
                   LEFT JOIN email_classifications ec
                     ON ec.message_identity=im.stable_message_identity
                   WHERE lower(j.company)=lower(?)
                   UNION ALL
                   SELECT rcl.last_seen_at, 'recruiter', r.name, 1
                   FROM recruiter_company_links rcl JOIN recruiters r ON r.id=rcl.recruiter_id
                   WHERE rcl.normalized_company_name=?
                   UNION ALL
                   SELECT COALESCE(o.offered_at,o.created_at), 'offer', o.status, 0
                   FROM offers o JOIN applications a ON a.id=o.application_id WHERE a.company_id=?
                   ORDER BY occurred_at DESC""",
                (
                    company_id,
                    company_id,
                    company_name,
                    company_name,
                    normalized_name,
                    company_id,
                ),
            )
        )
    return rows


def _company_context(connection: sqlite3.Connection, company_id: int) -> tuple[str, str]:
    row = connection.execute(
        "SELECT name, normalized_name FROM companies WHERE id=?", (company_id,)
    ).fetchone()
    if not row:
        raise LookupError("Company not found")
    return str(row[0]), str(row[1])


def upsert_recruiter_relationship(
    path: Path, recruiter_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    _require_schema(path)
    now = _now()
    with _connection(path, write=True) as connection:
        connection.execute(
            """INSERT INTO recruiter_relationships
               (recruiter_id, relationship_status, last_contact_at, next_follow_up_at,
                response_latency_hours, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(recruiter_id) DO UPDATE SET
               relationship_status=excluded.relationship_status,
               last_contact_at=excluded.last_contact_at,
               next_follow_up_at=excluded.next_follow_up_at,
               response_latency_hours=excluded.response_latency_hours,
               notes=excluded.notes, updated_at=excluded.updated_at""",
            (
                recruiter_id,
                payload.get("relationship_status", "active"),
                payload.get("last_contact_at"),
                payload.get("next_follow_up_at"),
                payload.get("response_latency_hours"),
                payload.get("notes", ""),
                now,
                now,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM recruiter_relationships WHERE recruiter_id=?", (recruiter_id,)
            ).fetchone()
        )


def get_recruiter_relationship(path: Path, recruiter_id: int) -> dict[str, Any] | None:
    if not schema_ready(path):
        return None
    with _connection(path) as connection:
        return _row(
            connection.execute(
                "SELECT * FROM recruiter_relationships WHERE recruiter_id=?",
                (recruiter_id,),
            )
        )


def version1_analytics(path: Path) -> dict[str, Any]:
    if not schema_ready(path):
        return {"schema_ready": False}
    with _connection(path) as connection:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("applications", "companies", "resumes", "offers", "interactions")
        }
        statuses = dict(
            connection.execute("SELECT status,COUNT(*) FROM applications GROUP BY status")
        )
        sources = dict(
            connection.execute("SELECT source,COUNT(*) FROM applications GROUP BY source")
        )
        resume_effectiveness = _rows(
            connection.execute(
                """SELECT r.name, COUNT(a.id) applications,
                    ROUND(AVG(a.match_score),2) average_match_score
                   FROM resumes r LEFT JOIN applications a ON a.resume_id=r.id GROUP BY r.id"""
            )
        )
    return {
        "schema_ready": True,
        "counts": counts,
        "application_statuses": statuses,
        "application_sources": sources,
        "resume_effectiveness": resume_effectiveness,
    }
