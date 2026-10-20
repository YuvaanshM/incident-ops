"""Real PostgreSQL fixtures for Phase 2 integration tests."""

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


def make_alembic_config(database_url: str) -> Config:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session", autouse=True)
def migrated_database(postgres_test_url: str) -> None:
    command.upgrade(make_alembic_config(postgres_test_url), "head")


@pytest.fixture(scope="session")
def database_engine(postgres_test_url: str) -> Iterator[AsyncEngine]:
    engine = create_async_engine(postgres_test_url, poolclass=NullPool)
    yield engine
    engine.sync_engine.dispose()


@pytest_asyncio.fixture
async def database_session(database_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture
async def clean_database(database_engine: AsyncEngine) -> AsyncIterator[None]:
    truncate_statement = text(
        """
        TRUNCATE
            investigation_runs,
            alert_events,
            alerts,
            outbox_events,
            idempotency_records,
            incident_events,
            action_requests,
            demo_jobs,
            historical_ingestion_events,
            historical_incidents,
            incidents
        RESTART IDENTITY
        """
    )
    async with database_engine.begin() as connection:
        await connection.execute(truncate_statement)
    yield
    async with database_engine.begin() as connection:
        await connection.execute(truncate_statement)
