from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ExtractionFieldDef(BaseModel):
    id: str
    label: str
    category: str = "General"
    retrieval_queries: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    evidence_hints: list[str] = Field(default_factory=list)
    prompt_instructions: str | None = None
    answer_style: Literal["text", "entity", "amount", "currency", "date", "summary", "jurisdiction", "rate"] = "text"
    post_processor: str | None = None
    retrieval_limit: int = 3

    @property
    def retrieval_text(self) -> str:
        parts = [
            self.label,
            *self.retrieval_queries,
            *self.aliases,
            *self.evidence_hints,
        ]
        return " ".join(dict.fromkeys(part.strip() for part in parts if part and part.strip()))


class ExtractionSchema(BaseModel):
    fields: list[ExtractionFieldDef]


@lru_cache(maxsize=8)
def _load_extraction_schema_resolved(path_str: str) -> ExtractionSchema:
    p = Path(path_str)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return ExtractionSchema.model_validate(data)


def load_extraction_schema(path: Path | None = None) -> ExtractionSchema:
    base = Path(__file__).resolve().parent
    p = (path or (base / "extraction_fields.yaml")).resolve()
    return _load_extraction_schema_resolved(str(p))
