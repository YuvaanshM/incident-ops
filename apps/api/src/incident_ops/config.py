"""Typed, fail-fast runtime configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_ops.infrastructure.monitoring_config import AlertIntakeSettings
from incident_ops.infrastructure.slack_config import SlackIntakeSettings

AppEnvironment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Application settings loaded from process environment and an optional local `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env", "apps/api/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter="__",
        hide_input_in_errors=True,
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

    agent_tokens_enabled: bool = False
    agent_token_issuer: str | None = Field(default=None, max_length=200)
    agent_token_audience: str | None = Field(default=None, max_length=200)
    agent_token_signing_key: SecretStr | None = None

    slack_webhook_url: SecretStr | None = None
    slack_intake: SlackIntakeSettings = Field(default_factory=SlackIntakeSettings)
    alert_intake: AlertIntakeSettings = Field(default_factory=AlertIntakeSettings)
    embedding_provider: Literal["disabled", "deterministic", "openai"] = "disabled"
    embedding_api_key: SecretStr | None = None
    embedding_allow_external_processing: bool = False
    embedding_model: str | None = Field(default=None, max_length=200)
    investigation_enabled: bool = False
    investigation_api_key: SecretStr | None = None
    investigation_model: Literal["gpt-5.4-mini-2026-03-17"] = "gpt-5.4-mini-2026-03-17"
    investigation_synthetic_data_confirmed: bool = False
    investigation_evaluation_passed: bool = False
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
        "embedding_api_key",
        "investigation_api_key",
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
    def validate_embedding_configuration(self) -> Self:
        models = {"deterministic": "hash-bow-v1", "openai": "text-embedding-3-small"}
        if self.embedding_provider == "disabled":
            return self
        if self.embedding_model != models[self.embedding_provider]:
            raise ValueError("embedding provider and exact model must match")
        if self.embedding_provider == "openai" and (
            self.embedding_api_key is None or not self.embedding_allow_external_processing
        ):
            raise ValueError("external embeddings require credentials and approved data processing")
        if self.embedding_api_key is not None:
            key = self.embedding_api_key.get_secret_value()
            if (
                not 1 <= len(key.encode()) <= 4096
                or not key.isascii()
                or not key.isprintable()
                or key.strip() != key
            ):
                raise ValueError("embedding credential must contain bounded printable bytes")
        return self

    @model_validator(mode="after")
    def validate_investigation_configuration(self) -> Self:
        if self.investigation_api_key is not None:
            key = self.investigation_api_key.get_secret_value()
            if (
                not 1 <= len(key.encode()) <= 4096
                or not key.isascii()
                or not key.isprintable()
                or key.strip() != key
            ):
                raise ValueError("investigation credential must contain bounded printable bytes")
        if self.investigation_enabled and (
            self.app_env == "production"
            or not self.agent_tokens_enabled
            or self.investigation_api_key is None
            or not self.investigation_synthetic_data_confirmed
            or not self.investigation_evaluation_passed
        ):
            raise ValueError(
                "investigation requires a synthetic non-production deployment, agent credentials, "
                "provider credentials and passed evaluation"
            )
        return self

    @model_validator(mode="after")
    def validate_agent_credentials(self) -> Self:
        if not self.agent_tokens_enabled:
            return self
        if (
            not self.agent_token_issuer
            or not self.agent_token_audience
            or not self.agent_token_signing_key
        ):
            raise ValueError("enabled agent credentials require issuer, audience and signing key")
        key = self.agent_token_signing_key.get_secret_value()
        if not 32 <= len(key.encode()) <= 4096 or not key.isprintable():
            raise ValueError("agent signing key must contain 32 to 4096 printable bytes")
        if self.oidc_issuer_url and self.agent_token_issuer.rstrip("/") == str(
            self.oidc_issuer_url
        ).rstrip("/"):
            raise ValueError("agent issuer must be distinct from the human issuer")
        if self.agent_token_audience == self.oidc_audience:
            raise ValueError("agent audience must be distinct from the human audience")
        return self

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
