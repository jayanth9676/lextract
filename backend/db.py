from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.config import get_settings


def _make_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite+aiosqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            engine = create_async_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
            )
        else:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            engine = create_async_engine(url, connect_args=connect_args)

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            if ":memory:" in url:
                return
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        return engine
    return create_async_engine(url)


engine = _make_engine()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    settings = get_settings()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        if settings.database_url.startswith("sqlite+aiosqlite") and ":memory:" not in settings.database_url:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_extracted_field_values_job_field "
                    "ON extracted_field_values (job_id, field_id)"
                )
            )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
