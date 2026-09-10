"""Typed, fail-fast runtime configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Application settings loaded from process environment and an optional local `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env", "apps/api/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnvironment = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_public_url: HttpUrl = HttpUrl("http://localhost:8000")
    web_public_url: HttpUrl = HttpUrl("http://localhost:3000")
    cors_origins: list[HttpUrl] = [HttpUrl("http://localhost:3000")]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]

    database_url: SecretStr

    oidc_issuer_url: HttpUrl | None = None
    oidc_audience: str | None = Field(default=None, max_length=200)
    oidc_jwks_url: HttpUrl | None = None
    oidc_actor_type_claim: str = Field(
        default="https://incident-ops.example/actor_type",
        min_length=1,
        max_length=200,
    )
    development_auth_enabled: bool = False

    agent_token_issuer: str | None = Field(default=None, max_length=200)
    agent_token_audience: str | None = Field(default=None, max_length=200)
    agent_token_signing_key: SecretStr | None = None

    slack_webhook_url: SecretStr | None = None
    embedding_provider: str = Field(default="disabled", min_length=1, max_length=100)
    embedding_model: str | None = Field(default=None, max_length=200)
    allow_development_reset: bool = False

    @field_validator(
        "oidc_issuer_url",
        "oidc_audience",
        "oidc_jwks_url",
        "agent_token_issuer",
        "agent_token_audience",
        "agent_token_signing_key",
        "slack_webhook_url",
        "embedding_model",
        mode="before",
    )
    @classmethod
    def normalize_blank_optional_value(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value()
        allowed_schemes = ("postgresql://", "postgresql+asyncpg://")
        if not raw_value.startswith(allowed_schemes):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        if any(character in raw_value for character in ("\n", "\r", "\x00")):
            raise ValueError("DATABASE_URL contains invalid characters")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: list[str]) -> list[str]:
        if not value or any(not host.strip() or host == "*" for host in value):
            raise ValueError("ALLOWED_HOSTS must be a non-empty explicit allowlist")
        return value

    @model_validator(mode="after")
    def reject_unsafe_production_configuration(self) -> Self:
        if self.app_env != "production":
            return self

        if self.development_auth_enabled or self.allow_development_reset:
            raise ValueError(
                "development authentication and fixture reset are forbidden in production"
            )

        if not self.oidc_issuer_url or not self.oidc_audience or not self.oidc_jwks_url:
            raise ValueError("OIDC settings are required in production")

        public_urls = (self.api_public_url, self.web_public_url)
        if any(url.scheme != "https" for url in public_urls):
            raise ValueError("public URLs must use HTTPS in production")

        return self

    @property
    def cors_origin_strings(self) -> list[str]:
        """Return exact CORS origins without trailing slashes."""

        return [str(origin).rstrip("/") for origin in self.cors_origins]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process."""

    return Settings()  # type: ignore[call-arg]
