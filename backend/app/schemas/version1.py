"""Typed request schemas for the Version 1 daily-use domain APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApplicationInput(BaseModel):
    job_id: int
    resume_id: int | None = None
    status: Literal["applied", "recruiter", "interview", "offer", "rejected", "withdrawn"] = (
        "applied"
    )
    applied_at: datetime | None = None
    source: str = ""
    notes: str = ""


class ApplicationUpdate(BaseModel):
    resume_id: int | None = None
    status: (
        Literal["applied", "recruiter", "interview", "offer", "rejected", "withdrawn"] | None
    ) = None
    applied_at: datetime | None = None
    source: str | None = None
    notes: str | None = None


class ResumeInput(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=100)
    family: str = ""
    tags: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    content_text: str = ""
    active: bool = True


class JobDescriptionInput(BaseModel):
    job_id: int
    source_type: Literal["text", "html", "pdf"] = "text"
    source_url: str = ""
    raw_text: str = Field(min_length=1)
    source_metadata: dict[str, object] = Field(default_factory=dict)


class OfferInput(BaseModel):
    application_id: int
    status: Literal["received", "negotiating", "accepted", "declined", "expired"] = "received"
    base_salary: float | None = None
    bonus: float | None = None
    equity: str = ""
    currency: str = "USD"
    offered_at: datetime | None = None
    expires_at: datetime | None = None
    notes: str = ""


class OfferUpdate(BaseModel):
    status: Literal["received", "negotiating", "accepted", "declined", "expired"] | None = None
    base_salary: float | None = None
    bonus: float | None = None
    equity: str | None = None
    currency: str | None = None
    expires_at: datetime | None = None
    notes: str | None = None


class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    website: str = ""
    industry: str = ""
    notes: str = ""


class NoteInput(BaseModel):
    entity_type: str
    entity_id: int
    body: str = Field(min_length=1)


class InteractionInput(BaseModel):
    company_id: int | None = None
    application_id: int | None = None
    recruiter_id: int | None = None
    job_id: int | None = None
    interview_id: int | None = None
    offer_id: int | None = None
    interaction_type: str
    occurred_at: datetime
    summary: str = ""


class RecruiterRelationshipInput(BaseModel):
    relationship_status: Literal["active", "warm", "dormant", "closed"] = "active"
    last_contact_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    response_latency_hours: float | None = None
    notes: str = ""
