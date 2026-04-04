from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from uuid import uuid4

from docling.chunking import HybridChunker
from docling.document_converter import DocumentConverter


@dataclass(slots=True)
class ParsedChunk:
    id: str
    chunk_tier: str
    text: str
    clause_id: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    metadata: dict

    def to_metadata(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ParsedDocument:
    chunks: list[ParsedChunk]
    markdown: str
    html: str
    text: str


class DoclingService:
    """Parse loan agreements into citation-friendly chunk records."""

    def __init__(self) -> None:
        self.converter = DocumentConverter()
        self.clause_chunker = HybridChunker(max_tokens=450, merge_peers=True)

    def extract_document(self, file_path: Path) -> ParsedDocument:
        result = self.converter.convert(str(file_path))
        document = result.document

        tier_a = self._build_clause_chunks(document)
        tier_b = self._build_section_chunks(tier_a)
        return ParsedDocument(
            chunks=[*tier_a, *tier_b],
            markdown=document.export_to_markdown(),
            html=document.export_to_html(),
            text=document.export_to_text(),
        )

    def extract_chunks(self, file_path: Path) -> list[ParsedChunk]:
        return self.extract_document(file_path).chunks

    def _build_clause_chunks(self, document) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []

        for raw_chunk in self.clause_chunker.chunk(document):
            text = self._contextual_text(raw_chunk).strip()
            if not text or self._is_noise_chunk(text):
                continue

            metadata = self._metadata_from_chunk(raw_chunk, text)
            for part in self._split_text(text):
                chunks.append(
                    ParsedChunk(
                        id=str(uuid4()),
                        chunk_tier="A",
                        text=part,
                        clause_id=metadata.get("clause_id"),
                        section_title=metadata.get("section_title"),
                        page_start=metadata.get("page_start"),
                        page_end=metadata.get("page_end"),
                        token_count=self._estimate_tokens(part),
                        metadata=metadata,
                    )
                )

        return chunks

    def _build_section_chunks(self, clause_chunks: list[ParsedChunk]) -> list[ParsedChunk]:
        if not clause_chunks:
            return []

        tier_b: list[ParsedChunk] = []
        buffer: list[ParsedChunk] = []
        current_title: str | None = None
        current_tokens = 0

        def flush() -> None:
            nonlocal buffer, current_title, current_tokens
            if not buffer:
                return

            text = "\n\n".join(chunk.text for chunk in buffer)
            tier_b.append(
                ParsedChunk(
                    id=str(uuid4()),
                    chunk_tier="B",
                    text=text,
                    clause_id=buffer[0].clause_id,
                    section_title=current_title,
                    page_start=buffer[0].page_start,
                    page_end=buffer[-1].page_end,
                    token_count=self._estimate_tokens(text),
                    metadata={
                        "section_title": current_title,
                        "source_chunk_ids": [chunk.id for chunk in buffer],
                    },
                )
            )
            buffer = []
            current_title = None
            current_tokens = 0

        for chunk in clause_chunks:
            section_title = chunk.section_title or "General"
            projected_tokens = current_tokens + chunk.token_count

            if buffer and (section_title != current_title or projected_tokens > 7500):
                flush()

            if not buffer:
                current_title = section_title

            buffer.append(chunk)
            current_tokens += chunk.token_count

        flush()
        return tier_b

    def _contextual_text(self, raw_chunk) -> str:
        contextualize = getattr(self.clause_chunker, "contextualize", None)
        if callable(contextualize):
            try:
                return str(contextualize(raw_chunk))
            except Exception:
                pass

        for attr in ("text", "chunk", "content"):
            value = getattr(raw_chunk, attr, None)
            if value:
                return str(value)

        return str(raw_chunk)

    def _metadata_from_chunk(self, raw_chunk, text: str) -> dict:
        meta = getattr(raw_chunk, "meta", None)
        headings = list(getattr(meta, "headings", []) or [])
        doc_items = list(getattr(meta, "doc_items", []) or [])

        page_numbers = []
        clause_candidates = []
        for item in doc_items:
            label = getattr(item, "label", None)
            if label:
                clause_candidates.append(str(label))

            prov = getattr(item, "prov", None)
            if prov:
                page_no = getattr(prov, "page_no", None)
                if page_no:
                    page_numbers.append(int(page_no))

        section_title = " > ".join(str(h) for h in headings if h) or None
        clause_id = clause_candidates[0] if clause_candidates else None
        inferred_clause_id, inferred_section_title = self._infer_text_metadata(text)
        if not clause_id or clause_id in {"text", "table"}:
            clause_id = inferred_clause_id
        if not section_title:
            section_title = inferred_section_title

        return {
            "section_title": section_title,
            "clause_id": clause_id,
            "page_start": min(page_numbers) if page_numbers else None,
            "page_end": max(page_numbers) if page_numbers else None,
            "headings": [str(h) for h in headings if h],
        }

    def _infer_text_metadata(self, text: str) -> tuple[str | None, str | None]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None, None

        for line in lines[:8]:
            section_match = re.search(
                r"\bSECTION\s+(\d+(?:\.\d+)*)\b[\s\.\-:]*([A-Z][^\n]{0,140})?",
                line,
                flags=re.IGNORECASE,
            )
            if section_match:
                clause_id = f"SECTION {section_match.group(1)}"
                title = line[:160]
                return clause_id, title

            clause_match = re.match(r"^((?:\d+\.)*\d+)\s+(.{3,160})$", line)
            if clause_match:
                clause_id = clause_match.group(1)
                return clause_id, line[:160]

            article_match = re.search(r"\bARTICLE\s+([IVXLC]+)\b[\s\.\-:]*([A-Z][^\n]{0,140})?", line, flags=re.IGNORECASE)
            if article_match:
                clause_id = f"ARTICLE {article_match.group(1).upper()}"
                return clause_id, line[:160]

        return None, lines[0][:160]

    def _split_text(self, text: str, max_tokens: int = 420, overlap_tokens: int = 40) -> list[str]:
        if self._estimate_tokens(text) <= max_tokens:
            return [text]

        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        if not paragraphs:
            return [text]

        pieces: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current_parts, current_tokens
            if current_parts:
                pieces.append("\n\n".join(current_parts).strip())
                current_parts = []
                current_tokens = 0

        for paragraph in paragraphs:
            paragraph_tokens = self._estimate_tokens(paragraph)
            if paragraph_tokens > max_tokens:
                flush()
                pieces.extend(self._split_long_paragraph(paragraph, max_tokens=max_tokens, overlap_tokens=overlap_tokens))
                continue

            if current_parts and current_tokens + paragraph_tokens > max_tokens:
                flush()

            current_parts.append(paragraph)
            current_tokens += paragraph_tokens

        flush()
        return pieces or [text]

    def _split_long_paragraph(
        self,
        paragraph: str,
        *,
        max_tokens: int,
        overlap_tokens: int,
    ) -> list[str]:
        words = paragraph.split()
        if not words:
            return []

        window_words = max(1, (max_tokens * 3) // 4)
        overlap_words = max(1, (overlap_tokens * 3) // 4)
        step = max(1, window_words - overlap_words)

        slices: list[str] = []
        for start in range(0, len(words), step):
            piece = " ".join(words[start : start + window_words]).strip()
            if piece:
                slices.append(piece)
            if start + window_words >= len(words):
                break
        return slices

    def _is_noise_chunk(self, text: str) -> bool:
        sample = text[:1500]
        upper_sample = sample.upper()

        # Skip obvious table-of-contents / repeated index artifacts from DOCX exports.
        if sample.count(" = ") >= 4:
            return True
        if upper_sample.count("SECTION") >= 4 and sample.count(",") >= 8:
            return True
        if "TABLE OF CONTENTS" in upper_sample:
            return True
        return False

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.split()) * 4 // 3)
