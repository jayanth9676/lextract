from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True, index=True)
    filename: str
    original_filename: str
    mime_type: str | None = None
    status: str = Field(default="pending", index=True)
    file_path: str
    error_message: str | None = None
    progress: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunks"

    id: str = Field(primary_key=True)
    job_id: str = Field(foreign_key="jobs.id", index=True)
    chunk_tier: str = Field(index=True)
    clause_id: str | None = None
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    token_count: int = 0
    text: str
    chunk_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class ExtractedFieldValue(SQLModel, table=True):
    __tablename__ = "extracted_field_values"
    __table_args__ = (Index("ix_extracted_field_values_job_field", "job_id", "field_id"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="jobs.id", index=True)
    field_id: str = Field(index=True)
    value: str | None = None
    confidence: float | None = None
    source_clause: str | None = None
    source_section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    is_absent: bool = False
    verbatim_evidence: str | None = None
    chunk_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    extra: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="jobs.id", index=True)
    field_id: str = Field(index=True)
    previous_value: str | None = None
    new_value: str | None = None
    user_id: str = Field(default="anonymous")
    created_at: datetime = Field(default_factory=utcnow)
