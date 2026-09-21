"""Explicit, secret-aware Slack intake configuration; separate from outbound delivery."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from incident_ops.domain.enums import IncidentSeverity

SlackId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]{2,31}$")]


class SlackChannelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    channel_id: Annotated[str, Field(pattern=r"^[CG][A-Z0-9]{2,31}$")]
    service: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    severity: IncidentSeverity = IncidentSeverity.MEDIUM


class SlackIntakeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    enabled: bool = False
    signing_secret: SecretStr | None = Field(default=None, repr=False)
    app_id: Annotated[str, Field(pattern=r"^A[A-Z0-9]{2,31}$")] | None = None
    team_id: Annotated[str, Field(pattern=r"^T[A-Z0-9]{2,31}$")] | None = None
    channels: tuple[SlackChannelBinding, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if len({channel.channel_id for channel in self.channels}) != len(self.channels):
            raise ValueError("Slack channel bindings must be unique")
        if self.enabled:
            if not self.signing_secret or not self.app_id or not self.team_id or not self.channels:
                raise ValueError("Slack intake requires a signing secret, app, team, and channels")
            secret = self.signing_secret.get_secret_value()
            if not 32 <= len(secret) <= 256 or not secret.isascii() or not secret.isalnum():
                raise ValueError("Slack signing secret has an invalid format")
        return self

    def channel(self, channel_id: str) -> SlackChannelBinding | None:
        return next((item for item in self.channels if item.channel_id == channel_id), None)
