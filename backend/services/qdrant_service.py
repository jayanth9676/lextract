from __future__ import annotations

from functools import lru_cache
import re
from uuid import UUID

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models

from backend.config import get_settings
from backend.models import DocumentChunk
from backend.schemas import ExtractionFieldDef


class QdrantService:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=60,
        )
        self.dense_model = _get_dense_model(settings.dense_model)
        self.sparse_model = _get_sparse_model(settings.sparse_model)
        self._collection_ready = False

    def ensure_collection(self) -> None:
        """Create collection and payload indexes once per process (idempotent)."""
        if self._collection_ready:
            return
        if not self.client.collection_exists(self.settings.qdrant_collection):
            vector_size = len(next(self.dense_model.embed(["dimension probe"])))
            self.client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config={
                    "dense": models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={"sparse": models.SparseVectorParams()},
                on_disk_payload=True,
            )

        self._ensure_payload_indexes()
        self._collection_ready = True

    def index_chunks(self, job_id: str, chunks: list[DocumentChunk]) -> None:
        self._ensure_ready()
        texts = [chunk.text for chunk in chunks]
        dense_vectors = list(self.dense_model.embed(texts))
        sparse_vectors = list(self.sparse_model.embed(texts))

        points: list[models.PointStruct] = []
        for chunk, dense_vector, sparse_vector in zip(chunks, dense_vectors, sparse_vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=UUID(chunk.id),
                    vector={
                        "dense": dense_vector.tolist(),
                        "sparse": models.SparseVector(
                            indices=sparse_vector.indices.tolist(),
                            values=sparse_vector.values.tolist(),
                        ),
                    },
                    payload={
                        "job_id": job_id,
                        "chunk_id": chunk.id,
                        "chunk_tier": chunk.chunk_tier,
                        "clause_id": chunk.clause_id,
                        "section_title": chunk.section_title,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "text": chunk.text,
                        "metadata": chunk.chunk_metadata,
                    },
                )
            )

        self.client.upsert(
            collection_name=self.settings.qdrant_collection,
            points=points,
            wait=True,
        )

    def _ensure_ready(self) -> None:
        if not self._collection_ready:
            self.ensure_collection()

    def retrieve_field_chunks(self, job_id: str, field_def: ExtractionFieldDef) -> list[dict]:
        self._ensure_ready()
        query_text = field_def.retrieval_text or field_def.label
        target_limit = max(1, field_def.retrieval_limit or self.settings.retrieval_limit)
        candidate_limit = max(target_limit * 10, 40)

        all_hits = list(self._query_candidates(job_id, query_text, candidate_limit))
        if field_def.evidence_hints:
            hint_query = " ".join(field_def.evidence_hints)
            all_hits.extend(self._query_candidates(job_id, hint_query, candidate_limit))

        deduped_hits = []
        seen_chunk_ids: set[str] = set()
        for point in all_hits:
            payload = point.payload or {}
            chunk_id = str(payload.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            deduped_hits.append(point)

        ranked_hits = sorted(
            (point for point in deduped_hits if point.payload),
            key=lambda point: self._rerank_candidate(field_def, point),
            reverse=True,
        )
        return [dict(point.payload) for point in ranked_hits[:target_limit] if point.payload]

    @staticmethod
    def _job_filter(job_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="job_id",
                    match=models.MatchValue(value=job_id),
                ),
                models.FieldCondition(
                    key="chunk_tier",
                    match=models.MatchValue(value="A"),
                ),
            ]
        )

    def _ensure_payload_indexes(self) -> None:
        for field_name in ("job_id", "chunk_tier"):
            try:
                self.client.create_payload_index(
                    collection_name=self.settings.qdrant_collection,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception as exc:
                err = str(exc).lower()
                if "already" in err or "duplicate" in err or "exists" in err or "409" in err:
                    continue
                raise

    def _query_candidates(self, job_id: str, query_text: str, candidate_limit: int):
        dense_query = next(self.dense_model.query_embed(query_text))
        sparse_query = next(self.sparse_model.query_embed(query_text))
        response = self.client.query_points(
            collection_name=self.settings.qdrant_collection,
            prefetch=[
                models.Prefetch(
                    query=dense_query.tolist(),
                    using="dense",
                    limit=60,
                    filter=self._job_filter(job_id),
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_query.indices.tolist(),
                        values=sparse_query.values.tolist(),
                    ),
                    using="sparse",
                    limit=60,
                    filter=self._job_filter(job_id),
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=candidate_limit,
        )
        return getattr(response, "points", response)

    @staticmethod
    def _rerank_candidate(field_def: ExtractionFieldDef, point) -> float:
        payload = point.payload or {}
        text = " ".join(
            str(payload.get(key) or "")
            for key in ("clause_id", "section_title", "text")
        ).lower()
        score = float(getattr(point, "score", 0.0) or 0.0)

        for term in field_def.aliases:
            if term.lower() in text:
                score += 0.2

        for hint in field_def.evidence_hints:
            if hint.lower() in text:
                score += 0.45

        if field_def.answer_style == "entity" and ("entered into among" in text or "preamble" in text):
            score += 0.4
        if field_def.answer_style == "date" and re.search(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}\b", text):
            score += 0.4
        if field_def.answer_style == "currency" and ("united states dollars" in text or "lawful money" in text):
            score += 0.4
        if field_def.answer_style == "amount" and "$" in text and "not less than" not in text and "letter of credit" not in text:
            score += 0.25

        return score


@lru_cache
def _get_dense_model(model_name: str) -> TextEmbedding:
    return TextEmbedding(model_name=model_name, providers=["CPUExecutionProvider"])


@lru_cache
def _get_sparse_model(model_name: str) -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name=model_name, providers=["CPUExecutionProvider"])
