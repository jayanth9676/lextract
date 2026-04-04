from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ExtractionResult(OrmModel):
    value: str | None = None
    confidence: float | None = None
    source_clause: str | None = None
    source_section: str | None = None
    page_range: str | None = None
    is_absent: bool = False
    verbatim_evidence: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)


class FieldValueResponse(ExtractionResult):
    field_id: str
    label: str
    category: str
    extra: dict[str, Any] = Field(default_factory=dict)


class JobResponse(OrmModel):
    id: str
    filename: str
    original_filename: str
    status: str
    progress: float
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class JobResultResponse(OrmModel):
    job: JobResponse
    fields: list[FieldValueResponse]
    document_url: str
    preview_url: str


class UpdateFieldRequest(BaseModel):
    value: str | None = None
    confidence: float | None = None


class AuditEventResponse(OrmModel):
    field_id: str
    previous_value: str | None
    new_value: str | None
    user_id: str
    created_at: datetime
