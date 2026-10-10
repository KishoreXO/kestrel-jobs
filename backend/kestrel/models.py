from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Profile(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    resume_text: str = ""
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str
    company: str = ""
    title: str = ""
    location: str = ""
    remote: bool | None = None
    url: str = ""
    apply_url: str = ""
    description: str = ""
    salary: str = ""
    department: str = ""
    posted_at: datetime | None = None
    attribution_text: str = ""
    attribution_url: str = ""
    # Board identifier (e.g. Greenhouse board token) used to fetch application questions.
    board: str = ""
    content_hash: str = Field(default="", index=True)
    dedupe_key: str = Field(default="", index=True)
    hidden: bool = False
    fetched_at: datetime = Field(default_factory=utcnow)
    first_seen: datetime | None = Field(default_factory=utcnow)
    # Application tracker (local only; Kestrel never acts on the employer's site).
    status: str = Field(default="new", index=True)
    notes: str = ""
    status_changed_at: datetime | None = None
    # Thumbs up (1) / down (-1) from the user; fed back into preference matching.
    feedback: int = 0


class Classification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, foreign_key="job.id")
    backend: str = ""
    model: str = ""
    tier: str = Field(default="", index=True)  # strong | good | rejected ("maybe" only in data from older versions)
    match_score: float = 0.0
    confidence: float = 0.0
    reject_reason: str = ""
    ai_prohibited: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    job_hash: str = ""
    profile_hash: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class AnswerDraft(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, foreign_key="job.id")
    question: str
    kind: str = ""
    answer: str = ""
    note: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    method: str = "GET"
    url: str = ""
    domain: str = Field(default="", index=True)
    purpose: str = ""
    status: int | None = None
    blocked_reason: str = ""


class FetchState(SQLModel, table=True):
    key: str = Field(primary_key=True)
    last_fetched: datetime | None = None
    etag: str = ""
    cooldown_until: datetime | None = None


class ScanRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "running"
    fetched: int = 0
    new: int = 0
    classified: int = 0
    log: list[str] = Field(default_factory=list, sa_column=Column(JSON))


def require_id(row: SQLModel) -> int:
    """The primary key of a row that has been flushed to the database."""
    ident = getattr(row, "id", None)
    if ident is None:
        raise ValueError(f"{type(row).__name__} has no id yet; flush the session first")
    return ident
