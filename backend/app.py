from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from openpyxl import Workbook
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.api_models import (
    AuditEventResponse,
    FieldValueResponse,
    JobResponse,
    JobResultResponse,
    UpdateFieldRequest,
)
from backend.config import get_settings
from backend.db import get_session, init_db
from backend.models import AuditEvent, ExtractedFieldValue, Job
from backend.schemas import load_extraction_schema
from backend.services.pipeline import pipeline_service
from backend.services.storage import save_upload


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    pipeline_service.qdrant.ensure_collection()
    yield


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse.model_validate(job)


def _preview_path(job: Job, format: str) -> Path:
    suffix_map = {"html": "preview.html", "markdown": "preview.md", "text": "preview.txt"}
    return Path(job.file_path).parent / suffix_map[format]


EXPORT_COLUMNS = [
    "field_id",
    "label",
    "category",
    "value",
    "confidence",
    "source_clause",
    "source_section",
    "page_range",
    "is_absent",
]


def _serialize_export_rows(fields: list[FieldValueResponse]) -> list[list[str]]:
    rows: list[list[str]] = []
    for field in fields:
        rows.append(
            [
                field.field_id,
                field.label,
                field.category,
                field.value or "",
                "" if field.confidence is None else str(field.confidence),
                field.source_clause or "",
                field.source_section or "",
                field.page_range or "",
                str(field.is_absent).lower(),
            ]
        )
    return rows


@app.get("/api/v1/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/jobs", response_model=JobResponse)
async def create_job(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    filename = file.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=400, detail="Only .pdf and .docx files are supported.")

    job_id = uuid4().hex
    stored_path = await save_upload(job_id, file)
    job = Job(
        id=job_id,
        filename=stored_path.name,
        original_filename=filename,
        mime_type=file.content_type,
        status="queued",
        progress=0.0,
        file_path=str(stored_path),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await pipeline_service.enqueue(job_id)
    return _job_to_response(job)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session)) -> JobResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_response(job)


@app.get("/api/v1/jobs/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(job_id: str, session: AsyncSession = Depends(get_session)) -> JobResultResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    schema = load_extraction_schema()
    field_rows = (
        await session.exec(
            select(ExtractedFieldValue).where(ExtractedFieldValue.job_id == job_id)
        )
    ).all()
    row_map = {row.field_id: row for row in field_rows}

    fields: list[FieldValueResponse] = []
    if field_rows or job.status == "completed":
        for field_def in schema.fields:
            row = row_map.get(field_def.id)
            page_range = None
            if row and row.page_start:
                page_range = f"{row.page_start}-{row.page_end or row.page_start}"

            fields.append(
                FieldValueResponse(
                    field_id=field_def.id,
                    label=field_def.label,
                    category=field_def.category,
                    value=row.value if row else None,
                    confidence=row.confidence if row else None,
                    source_clause=row.source_clause if row else None,
                    source_section=row.source_section if row else None,
                    page_range=page_range,
                    is_absent=row.is_absent if row else False,
                    verbatim_evidence=row.verbatim_evidence if row else None,
                    chunk_ids=row.chunk_ids if row else [],
                    extra=row.extra if row else {},
                )
            )

    return JobResultResponse(
        job=_job_to_response(job),
        fields=fields,
        document_url=f"/api/v1/jobs/{job_id}/document",
        preview_url=f"/api/v1/jobs/{job_id}/preview?format=html",
    )


@app.patch("/api/v1/jobs/{job_id}/fields/{field_id}", response_model=AuditEventResponse)
async def update_field(
    job_id: str,
    field_id: str,
    payload: UpdateFieldRequest,
    x_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuditEventResponse:
    row = (
        await session.exec(
            select(ExtractedFieldValue).where(
                ExtractedFieldValue.job_id == job_id,
                ExtractedFieldValue.field_id == field_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Field result not found.")

    previous_value = row.value
    row.value = payload.value
    row.confidence = payload.confidence if payload.confidence is not None else row.confidence

    audit = AuditEvent(
        job_id=job_id,
        field_id=field_id,
        previous_value=previous_value,
        new_value=payload.value,
        user_id=x_user_id or "anonymous",
    )
    session.add(row)
    session.add(audit)

    job = await session.get(Job, job_id)
    if job:
        job.updated_at = utcnow()
        session.add(job)

    await session.commit()
    await session.refresh(audit)

    return AuditEventResponse.model_validate(audit)


@app.get("/api/v1/jobs/{job_id}/export")
async def export_job_result(
    job_id: str,
    format: str = Query(default="json", pattern="^(json|csv|xlsx)$"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    result = await get_job_result(job_id, session)
    if format == "json":
        return Response(
            content=result.model_dump_json(indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
        )

    rows = _serialize_export_rows(result.fields)

    if format == "xlsx":
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Extracted Entities"
        worksheet.append(EXPORT_COLUMNS)
        for row in rows:
            worksheet.append(row)

        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.xlsx"'},
        )

    lines = [",".join(EXPORT_COLUMNS)]
    for row in rows:
        lines.append(",".join(f'"{part.replace("\"", "\"\"")}"' for part in row))

    return Response(
        content="\n".join(lines),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
    )


@app.get("/api/v1/jobs/{job_id}/document")
async def get_document(job_id: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return FileResponse(path=job.file_path, filename=job.original_filename)


@app.get("/api/v1/jobs/{job_id}/preview")
async def get_document_preview(
    job_id: str,
    format: str = Query(default="html", pattern="^(html|markdown|text)$"),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    preview_path = _preview_path(job, format)
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Preview not available yet.")

    media_types = {
        "html": "text/html; charset=utf-8",
        "markdown": "text/markdown; charset=utf-8",
        "text": "text/plain; charset=utf-8",
    }
    return FileResponse(
        path=preview_path,
        media_type=media_types[format],
    )


@app.get("/api/v1/jobs/{job_id}/audit", response_model=list[AuditEventResponse])
async def list_audit_events(
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventResponse]:
    events = (
        await session.exec(select(AuditEvent).where(AuditEvent.job_id == job_id))
    ).all()
    return [AuditEventResponse.model_validate(event) for event in events]


@app.get("/")
async def root() -> PlainTextResponse:
    return PlainTextResponse("LexExtract API")
