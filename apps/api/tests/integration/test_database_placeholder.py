"""Phase 1 proof that the CI PostgreSQL service is reachable."""

import os

import asyncpg
import pytest


@pytest.mark.integration
async def test_postgres_service_is_reachable() -> None:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
    try:
        assert await connection.fetchval("SELECT 1") == 1
    finally:
        await connection.close()
