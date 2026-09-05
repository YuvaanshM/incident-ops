"""Shared test configuration."""

import os

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


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    """Return an explicitly disposable PostgreSQL URL for integration tests."""

    configured_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if configured_url is None:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required for integration tests")

    database_name = configured_url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
    if not database_name.endswith("_test"):
        pytest.fail("integration tests refuse a database whose name does not end in '_test'")
    return configured_url
