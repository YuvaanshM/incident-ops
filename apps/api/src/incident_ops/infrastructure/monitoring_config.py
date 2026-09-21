"""Secret-aware, source-bound configuration for generic alert ingestion."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

SafeSource = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
SafeService = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
]
SafeKeyId = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]


class AlertSigningKeySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    key_id: SafeKeyId
    signing_secret: SecretStr = Field(repr=False)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_secret(self) -> Self:
        secret = self.signing_secret.get_secret_value()
        if not 32 <= len(secret) <= 256 or not secret.isascii() or not secret.isprintable():
            raise ValueError("alert signing secret has an invalid format")
        return self


class AlertSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source: SafeSource
    services: tuple[SafeService, ...] = Field(min_length=1, max_length=50)
    keys: tuple[AlertSigningKeySettings, ...] = Field(min_length=1, max_length=5)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> Self:
        if len(set(self.services)) != len(self.services):
            raise ValueError("alert source services must be unique")
        key_ids = [key.key_id for key in self.keys]
        if len(set(key_ids)) != len(key_ids):
            raise ValueError("alert source key IDs must be unique")
        if self.enabled and not any(key.enabled for key in self.keys):
            raise ValueError("enabled alert source requires an enabled key")
        return self


class AlertIntakeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    enabled: bool = False
    sources: tuple[AlertSourceSettings, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        source_ids = [source.source for source in self.sources]
        key_ids = [key.key_id for source in self.sources for key in source.keys]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("alert source IDs must be unique")
        if len(set(key_ids)) != len(key_ids):
            raise ValueError("alert key IDs must be globally unique")
        if self.enabled and not any(source.enabled for source in self.sources):
            raise ValueError("alert intake requires an enabled source")
        return self

    def resolve_key(
        self, key_id: str
    ) -> tuple[AlertSourceSettings, AlertSigningKeySettings] | None:
        if not self.enabled:
            return None
        for source in self.sources:
            if not source.enabled:
                continue
            for key in source.keys:
                if key.enabled and key.key_id == key_id:
                    return source, key
        return None
