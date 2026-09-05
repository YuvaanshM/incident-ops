"""Configuration safety tests."""

import pytest
from pydantic import ValidationError

from incident_ops.config import Settings


def test_database_url_is_redacted(test_settings: Settings) -> None:
    assert "test:test" not in repr(test_settings)


def test_blank_optional_provider_values_are_not_configured() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql://test:test@localhost/test",
        oidc_issuer_url="",
        oidc_jwks_url="",
        slack_webhook_url="",
    )

    assert settings.oidc_issuer_url is None
    assert settings.oidc_jwks_url is None
    assert settings.slack_webhook_url is None


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError, match="must use PostgreSQL"):
        Settings(_env_file=None, app_env="test", database_url="sqlite:///unsafe.db")


def test_rejects_wildcard_allowed_host() -> None:
    with pytest.raises(ValidationError, match="explicit allowlist"):
        Settings(
            _env_file=None,
            app_env="test",
            database_url="postgresql://test:test@localhost/test",
            allowed_hosts=["*"],
        )


def test_rejects_development_flags_in_production() -> None:
    with pytest.raises(ValidationError, match="forbidden in production"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql://user:password@db.example.com/incident_ops",
            api_public_url="https://api.example.com",
            web_public_url="https://app.example.com",
            development_auth_enabled=True,
        )
