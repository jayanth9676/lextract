from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LexExtract API"
    app_env: str = "development"
    database_url: str = Field(default="sqlite+aiosqlite:///./data/lexextract.db")
    data_dir: Path = Field(default=Path("data"))
    uploads_subdir: str = "uploads"
    cors_origins: str = Field(default="http://localhost:3000")
    max_upload_size_mb: int = 50

    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str | None = None
    qdrant_collection: str = Field(default="loan_agreement_chunks")

    claude_api_key: str = Field(default="")
    claude_model: str = Field(default="claude-haiku-4-5")

    dense_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    sparse_model: str = Field(default="Qdrant/bm25")

    extraction_concurrency: int = Field(default=6)
    retrieval_concurrency: int = Field(
        default=8,
        description="Max concurrent Qdrant retrieval calls per job (embedding + hybrid search per field).",
    )
    max_concurrent_jobs: int = Field(
        default=2,
        description="Max pipeline jobs processing at once (Docling + Qdrant + Claude).",
    )
    retrieval_limit: int = Field(default=3)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / self.uploads_subdir


@lru_cache
def get_settings() -> Settings:
    return Settings()
