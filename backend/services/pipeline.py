from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from sqlmodel import delete, select

from backend.api_models import ExtractionResult
from backend.config import get_settings
from backend.db import async_session_maker
from backend.models import DocumentChunk, ExtractedFieldValue, Job
from backend.schemas import ExtractionFieldDef, load_extraction_schema
from backend.services.claude_service import ClaudeService
from backend.services.docling_service import DoclingService
from backend.services.qdrant_service import QdrantService


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SUPPLEMENT_DATE_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TierAChunkIndex:
    """Precomputed tier-A rows: one combined lowercase string per chunk (parse once per job)."""

    entries: list[tuple[DocumentChunk, str]]

    @classmethod
    def from_chunks(cls, all_chunks: list[DocumentChunk]) -> TierAChunkIndex:
        tier_a: list[tuple[DocumentChunk, str]] = []
        for chunk in all_chunks:
            if chunk.chunk_tier != "A":
                continue
            combined = " ".join(
                part for part in (chunk.clause_id, chunk.section_title, chunk.text) if part
            ).lower()
            tier_a.append((chunk, combined))
        return cls(tier_a)


class PipelineService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.docling = DoclingService()
        self.qdrant = QdrantService()
        self.claude = ClaudeService()
        self.schema = load_extraction_schema()
        self._job_sem = asyncio.Semaphore(max(1, self.settings.max_concurrent_jobs))

    async def enqueue(self, job_id: str) -> None:
        asyncio.create_task(self._run_job_with_slot(job_id))

    async def _run_job_with_slot(self, job_id: str) -> None:
        async with self._job_sem:
            await self.process_job(job_id)

    async def process_job(self, job_id: str) -> None:
        try:
            await self._set_job_status(job_id, status="parsing", progress=0.1)
            job = await self._get_job(job_id)
            if job is None:
                return

            parsed_document = await asyncio.to_thread(self.docling.extract_document, Path(job.file_path))
            await asyncio.to_thread(self._write_preview_files, Path(job.file_path).parent, parsed_document)
            chunk_models = [
                DocumentChunk(
                    id=chunk.id,
                    job_id=job_id,
                    chunk_tier=chunk.chunk_tier,
                    clause_id=chunk.clause_id,
                    section_title=chunk.section_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    token_count=chunk.token_count,
                    text=chunk.text,
                    chunk_metadata=chunk.metadata,
                )
                for chunk in parsed_document.chunks
            ]

            await self._replace_chunks(job_id, chunk_models)
            await self._set_job_status(job_id, status="indexing", progress=0.4)
            await asyncio.to_thread(self.qdrant.index_chunks, job_id, chunk_models)

            await self._set_job_status(job_id, status="extracting", progress=0.65)
            tier_index = TierAChunkIndex.from_chunks(chunk_models)
            retrieved_chunks = await self._retrieve_all_field_chunks(job_id, self.schema.fields, tier_index)
            extracted = await self.claude.extract_many(self.schema.fields, retrieved_chunks)

            await self._replace_field_values(job_id, extracted)
            await self._set_job_status(job_id, status="completed", progress=1.0)
        except Exception as exc:
            await self._set_job_status(
                job_id,
                status="failed",
                progress=1.0,
                error_message=str(exc),
            )

    async def _retrieve_all_field_chunks(
        self,
        job_id: str,
        field_defs: list[ExtractionFieldDef],
        tier_index: TierAChunkIndex,
    ) -> dict[str, list[dict]]:
        sem = asyncio.Semaphore(max(1, self.settings.retrieval_concurrency))

        async def one(field: ExtractionFieldDef) -> tuple[str, list[dict]]:
            async with sem:
                field_chunks = await asyncio.to_thread(self.qdrant.retrieve_field_chunks, job_id, field)
            return field.id, self._supplement_field_chunks(field, field_chunks, tier_index)

        pairs = await asyncio.gather(*(one(f) for f in field_defs))
        return {fid: chunks for fid, chunks in pairs}

    async def _get_job(self, job_id: str) -> Job | None:
        async with async_session_maker() as session:
            return await session.get(Job, job_id)

    async def _replace_chunks(self, job_id: str, chunks: list[DocumentChunk]) -> None:
        async with async_session_maker() as session:
            await session.exec(delete(DocumentChunk).where(DocumentChunk.job_id == job_id))
            session.add_all(chunks)
            await session.commit()

    async def _replace_field_values(
        self,
        job_id: str,
        values: dict[str, ExtractionResult],
    ) -> None:
        async with async_session_maker() as session:
            await session.exec(delete(ExtractedFieldValue).where(ExtractedFieldValue.job_id == job_id))
            for field_id, result in values.items():
                page_start, page_end = self._parse_page_range(result.page_range)

                session.add(
                    ExtractedFieldValue(
                        job_id=job_id,
                        field_id=field_id,
                        value=result.value,
                        confidence=result.confidence,
                        source_clause=result.source_clause,
                        source_section=result.source_section,
                        page_start=page_start,
                        page_end=page_end,
                        is_absent=result.is_absent,
                        verbatim_evidence=result.verbatim_evidence,
                        chunk_ids=result.chunk_ids,
                        extra={},
                    )
                )
            await session.commit()

    async def _set_job_status(
        self,
        job_id: str,
        *,
        status: str,
        progress: float,
        error_message: str | None = None,
    ) -> None:
        async with async_session_maker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.progress = progress
            job.updated_at = utcnow()
            job.error_message = error_message
            session.add(job)
            await session.commit()

    @staticmethod
    def _write_preview_files(job_dir: Path, parsed_document) -> None:
        (job_dir / "preview.html").write_text(parsed_document.html, encoding="utf-8")
        (job_dir / "preview.md").write_text(parsed_document.markdown, encoding="utf-8")
        (job_dir / "preview.txt").write_text(parsed_document.text, encoding="utf-8")

    @staticmethod
    def _parse_page_range(page_range: str | None) -> tuple[int | None, int | None]:
        if not page_range:
            return None, None

        numbers = [int(value) for value in re.findall(r"\d+", page_range)]
        if not numbers:
            return None, None
        if len(numbers) == 1:
            return numbers[0], numbers[0]
        return numbers[0], numbers[1]

    @staticmethod
    def _supplement_field_chunks(
        field_def: ExtractionFieldDef,
        retrieved_chunks: list[dict],
        chunks_or_index: list[DocumentChunk] | TierAChunkIndex,
    ) -> list[dict]:
        tier_index = (
            chunks_or_index
            if isinstance(chunks_or_index, TierAChunkIndex)
            else TierAChunkIndex.from_chunks(chunks_or_index)
        )
        target_limit = max(1, field_def.retrieval_limit)
        existing_ids = {str(chunk.get("chunk_id") or "") for chunk in retrieved_chunks}
        supplemental: list[tuple[float, dict]] = []

        for chunk, text in tier_index.entries:
            score = 0.0

            for hint in field_def.evidence_hints:
                if hint.lower() in text:
                    score += 2.0
            for alias in field_def.aliases:
                if alias.lower() in text:
                    score += 0.25

            if field_def.answer_style == "entity" and "entered into among" in text:
                score += 1.0
            if field_def.answer_style == "date" and _SUPPLEMENT_DATE_RE.search(text):
                score += 1.0
            if field_def.answer_style == "currency" and ("united states dollars" in text or "lawful money" in text):
                score += 1.0
            if field_def.answer_style == "jurisdiction" and "laws of the state of" in text:
                score += 1.0

            if score <= 0:
                continue

            chunk_payload = {
                "chunk_id": chunk.id,
                "clause_id": chunk.clause_id,
                "section_title": chunk.section_title,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "text": chunk.text,
            }
            chunk_id = chunk.id
            if chunk_id in existing_ids:
                continue
            supplemental.append((score, chunk_payload))

        supplemental.sort(key=lambda item: item[0], reverse=True)
        primary_hint = field_def.evidence_hints[0].lower() if field_def.evidence_hints else None

        retrieved_lowers = [
            " ".join(str(chunk.get(key) or "") for key in ("clause_id", "section_title", "text")).lower()
            for chunk in retrieved_chunks
        ]
        retrieved_has_strong_hint = any(
            (primary_hint is not None and primary_hint in rl)
            or (field_def.answer_style == "entity" and "entered into among" in rl)
            for rl in retrieved_lowers
        )
        supplemental_chunks = [chunk for _, chunk in supplemental]
        merged = retrieved_chunks + supplemental_chunks if retrieved_has_strong_hint else supplemental_chunks + retrieved_chunks
        deduped: list[dict] = []
        seen_ids: set[str] = set()
        for chunk in merged:
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_ids:
                continue
            if chunk_id:
                seen_ids.add(chunk_id)
            deduped.append(chunk)
            if len(deduped) >= target_limit:
                break
        return deduped


pipeline_service = PipelineService()
