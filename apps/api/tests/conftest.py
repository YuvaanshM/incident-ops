"""Shared test configuration."""

import pytest

from incident_ops.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )
