"""Phase 1 proof that the CI PostgreSQL service is reachable."""

import asyncpg
import pytest


@pytest.mark.integration
async def test_postgres_service_is_reachable(postgres_test_url: str) -> None:
    database_url = postgres_test_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(database_url)
    try:
        assert await connection.fetchval("SELECT 1") == 1
    finally:
        await connection.close()
