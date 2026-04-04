from __future__ import annotations

import asyncio
from textwrap import dedent

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from backend.api_models import ExtractionResult
from backend.config import get_settings
from backend.schemas import ExtractionFieldDef
from backend.services.extraction_postprocessors import apply_field_post_processor


SYSTEM_PROMPT = dedent(
    """
    You extract structured loan-agreement facts from retrieved evidence.
    Requirements:
    - Use only the supplied evidence chunks.
    - If the field is not explicitly supported, return is_absent=true.
    - Never invent values, clauses, page ranges, or citations.
    - Keep verbatim_evidence as a short exact quote from the evidence.
    - source_clause and source_section must come from the provided chunk metadata.
    - Prefer exact legal names, defined dates, jurisdictions, and monetary amounts over summaries.
    - If the evidence only supports a generic defined term like "Company" or "Lender", use the
      surrounding text to recover the full legal entity name when it is explicitly stated.
    """
).strip()


class ClaudeExtraction(ExtractionResult):
    pass


class ClaudeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = AsyncAnthropic(api_key=self.settings.claude_api_key or "missing")
        self.semaphore = asyncio.Semaphore(self.settings.extraction_concurrency)

    async def extract_many(
        self,
        fields: list[ExtractionFieldDef],
        retrieved_chunks: dict[str, list[dict]],
    ) -> dict[str, ClaudeExtraction]:
        tasks = [
            self.extract_field(field_def=field, chunks=retrieved_chunks.get(field.id, []))
            for field in fields
        ]
        results = await asyncio.gather(*tasks)
        return {field.id: result for field, result in zip(fields, results, strict=True)}

    async def extract_field(
        self,
        field_def: ExtractionFieldDef,
        chunks: list[dict],
    ) -> ClaudeExtraction:
        if not self.settings.claude_api_key or not chunks:
            return apply_field_post_processor(field_def, self._fallback_result(chunks), chunks)

        evidence = "\n\n".join(
            [
                (
                    f"Chunk {idx + 1}\n"
                    f"Clause: {chunk.get('clause_id') or 'unknown'}\n"
                    f"Section: {chunk.get('section_title') or 'unknown'}\n"
                    f"Pages: {chunk.get('page_start')} - {chunk.get('page_end')}\n"
                    f"Text:\n{chunk.get('text', '')}"
                )
                for idx, chunk in enumerate(chunks)
            ]
        )

        async with self.semaphore:
            parsed = await self.client.messages.parse(
                model=self.settings.claude_model,
                max_tokens=800,
                temperature=0,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                output_format=ClaudeExtraction,
                messages=[
                    {
                        "role": "user",
                        "content": self._build_field_prompt(field_def, evidence),
                    }
                ],
            )

        result = self._populate_default_citations(parsed.parsed_output, chunks)
        return apply_field_post_processor(field_def, result, chunks)

    @staticmethod
    def _fallback_result(chunks: list[dict]) -> ClaudeExtraction:
        if not chunks:
            return ClaudeExtraction(is_absent=True)

        top = chunks[0]
        page_start = top.get("page_start")
        page_end = top.get("page_end")
        page_range = None
        if page_start:
            page_range = f"{page_start}-{page_end or page_start}"

        return ClaudeExtraction(
            value=None,
            confidence=0.0,
            source_clause=top.get("clause_id"),
            source_section=top.get("section_title"),
            page_range=page_range,
            is_absent=True,
            verbatim_evidence=(top.get("text") or "")[:400],
            chunk_ids=[top.get("chunk_id")] if top.get("chunk_id") else [],
        )

    @staticmethod
    def _build_field_prompt(field_def: ExtractionFieldDef, evidence: str) -> str:
        instructions = field_def.prompt_instructions or "Return the best-supported value from the evidence."
        aliases = ", ".join(field_def.aliases) or "none"
        evidence_hints = ", ".join(field_def.evidence_hints) or "none"
        return (
            f"Field: {field_def.label}\n"
            f"Field id: {field_def.id}\n"
            f"Answer style: {field_def.answer_style}\n"
            f"Retrieval hints: {', '.join(field_def.retrieval_queries)}\n"
            f"Aliases: {aliases}\n"
            f"Evidence hints: {evidence_hints}\n"
            f"Field-specific instructions: {instructions}\n\n"
            "Return a concise value. For summary fields, keep the answer short and precise.\n\n"
            f"Evidence:\n{evidence}"
        )

    @staticmethod
    def _populate_default_citations(result: ClaudeExtraction, chunks: list[dict]) -> ClaudeExtraction:
        if not chunks:
            return result

        top = chunks[0]
        available_chunk_ids = [chunk.get("chunk_id") for chunk in chunks if chunk.get("chunk_id")]
        page_start = top.get("page_start")
        page_end = top.get("page_end")
        if result.page_range is None and page_start:
            result.page_range = f"{page_start}-{page_end or page_start}"
        if result.source_clause in {None, "", "unknown", "text", "table"}:
            result.source_clause = top.get("clause_id")
        if result.source_section in {None, "", "unknown"}:
            result.source_section = top.get("section_title")
        if not result.chunk_ids or any(chunk_id not in available_chunk_ids for chunk_id in result.chunk_ids):
            result.chunk_ids = available_chunk_ids
        if result.verbatim_evidence is None:
            result.verbatim_evidence = (top.get("text") or "")[:400]
        return result
