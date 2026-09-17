"""Operator-owned delivery settings with finite, validated limits."""

from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from incident_ops.config import Settings


class WorkerSettings(Settings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    worker_batch_size: int = Field(default=5, ge=1, le=20)
    worker_poll_seconds: float = Field(default=2, ge=1, le=60, allow_inf_nan=False)
    worker_lease_seconds: float = Field(default=60, ge=30, le=600, allow_inf_nan=False)
    worker_max_attempts: int = Field(default=5, ge=1, le=25)
    worker_retry_base_seconds: float = Field(default=5, ge=1, le=300, allow_inf_nan=False)
    worker_retry_max_seconds: float = Field(default=300, ge=1, le=3600, allow_inf_nan=False)
    slack_connect_timeout_seconds: float = Field(default=3, ge=0.1, le=10, allow_inf_nan=False)
    slack_read_timeout_seconds: float = Field(default=10, ge=0.1, le=30, allow_inf_nan=False)
    slack_total_timeout_seconds: float = Field(default=15, ge=1, le=45, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_worker(self) -> Self:
        if self.slack_webhook_url is None:
            raise ValueError("worker requires SLACK_WEBHOOK_URL")
        raw = self.slack_webhook_url.get_secret_value()
        try:
            url = urlsplit(raw)
            port = url.port
            slack = (
                url.scheme == "https"
                and url.hostname in {"hooks.slack.com", "hooks.slack-gov.com"}
                and port in {None, 443}
                and url.path.startswith("/services/")
            )
            local = (
                self.app_env in {"development", "test"}
                and url.scheme == "http"
                and url.hostname
                in {"localhost", "127.0.0.1", "[::1]", "::1", "host.docker.internal"}
            )
            valid = (
                (slack or local)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and not any(c.isspace() for c in raw)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("invalid Slack webhook configuration")
        public = self.web_public_url
        if public.username or public.password or public.query or public.fragment:
            raise ValueError("invalid notification link origin")
        if self.worker_lease_seconds < self.slack_total_timeout_seconds + 15:
            raise ValueError("lease must exceed total HTTP timeout by at least 15 seconds")
        if self.worker_retry_base_seconds > self.worker_retry_max_seconds:
            raise ValueError("retry base exceeds retry maximum")
        return self
